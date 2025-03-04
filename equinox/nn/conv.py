import collections
from itertools import repeat
from typing import Any, Optional, Sequence, Tuple, Union

import jax
import jax.numpy as jnp
import jax.random as jrandom
import numpy as np
from jax.lax import conv_general_dilated, conv_transpose

from ..custom_types import Array
from ..module import Module, static_field


def _ntuple(n: int) -> callable:
    def parse(x: Any) -> tuple:
        if isinstance(x, collections.abc.Iterable):
            if len(x) == n:
                return tuple(x)
            else:
                raise ValueError(
                    f"Length of {x} (length = {len(x)}) is not equal to {n}"
                )
        return tuple(repeat(x, n))

    return parse


def compute_adjusted_padding(
    input_size: int,
    kernel_size: int,
    stride: int,
    padding: int,
    output_padding: int,
    dilation: int = 1,
) -> Tuple[int, int]:
    """Computes adjusted padding for desired ConvTranspose `output_padding`."""
    kernel_size = (kernel_size - 1) * dilation + 1
    output_size = (input_size - 1) * stride - 2 * padding + kernel_size + output_padding
    if padding == 0:
        expected_input_size = (output_size - kernel_size + stride) // stride
        if input_size != expected_input_size:
            raise ValueError(
                f"The expected input size with the current set of input "
                f"parameters is {expected_input_size} which doesn't "
                f"match the actual input size {input_size}."
            )
        padding_before = 0
    elif padding == 1:
        expected_input_size = (output_size + stride - 1) // stride
        if input_size != expected_input_size:
            raise ValueError(
                f"The expected input size with the current set of input "
                f"parameters is {expected_input_size} which doesn't "
                f"match the actual input size {input_size}."
            )
        padding_needed = max(0, (input_size - 1) * stride + kernel_size - output_size)
        padding_before = padding_needed // 2
    else:
        raise ValueError(f"`padding` must be '0' or '1'. Passed: {padding}.")

    expanded_input_size = (input_size - 1) * stride + 1
    padded_out_size = output_size + kernel_size - 1
    pad_before = kernel_size - 1 - padding_before
    pad_after = padded_out_size - expanded_input_size - pad_before
    return (pad_before, pad_after)


class Conv(Module):
    """General N-dimensional convolution."""

    num_spatial_dims: int = static_field()
    weight: Array
    bias: Optional[Array]
    in_channels: int = static_field()
    out_channels: int = static_field()
    kernel_size: Tuple[int] = static_field()
    stride: Tuple[int] = static_field()
    padding: Tuple[int] = static_field()
    dilation: Tuple[int] = static_field()
    use_bias: bool = static_field()

    def __init__(
        self,
        num_spatial_dims: int,
        in_channels: int,
        out_channels: int,
        kernel_size: Union[int, Sequence[int]],
        stride: Union[int, Sequence[int]] = 1,
        padding: Union[int, Sequence[int]] = 0,
        dilation: Union[int, Sequence[int]] = 1,
        use_bias: bool = True,
        *,
        key: "jax.random.PRNGKey",
        **kwargs,
    ):
        """**Arguments:**

        - `num_spatial_dims`: The number of spatial dimensions. For example traditional
            convolutions for image processing have this set to `2`.
        - `in_channels`: The number of input channels.
        - `out_channels`: The number of output channels.
        - `kernel_size`: The size of the convolutional kernel.
        - `stride`: The stride of the convolution.
        - `padding`: The amount of padding to apply before and after each spatial
            dimension. The same amount of padding is applied both before and after.
        - `dilation`: The dilation of the convolution.
        - `use_bias`: Whether to add on a bias after the convolution.
        - `key`: A `jax.random.PRNGKey` used to provide randomness for parameter
            initialisation. (Keyword only argument.)

        !!! info

            All of `kernel_size`, `stride`, `padding`, `dilation` can be either an
            integer or a sequence of integers. If they are a sequence then the sequence
            should be of length equal to `num_spatial_dims`, and specify the value of
            each property down each spatial dimension in turn. If they are an integer
            then the same kernel size / stride / padding / dilation will be used along
            every spatial dimension.

        """
        super().__init__(**kwargs)
        self.num_spatial_dims = num_spatial_dims
        parse = _ntuple(self.num_spatial_dims)
        wkey, bkey = jrandom.split(key, 2)
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel_size = parse(kernel_size)
        self.use_bias = use_bias
        lim = 1 / np.sqrt(self.in_channels * np.prod(self.kernel_size))

        self.weight = jrandom.uniform(
            wkey,
            (
                self.out_channels,
                self.in_channels,
            )
            + self.kernel_size,
            minval=-lim,
            maxval=lim,
        )
        if self.use_bias:
            self.bias = jrandom.uniform(
                bkey,
                (self.out_channels,) + (1,) * self.num_spatial_dims,
                minval=-lim,
                maxval=lim,
            )
        else:
            self.bias = None

        self.stride = parse(stride)
        if isinstance(padding, int):
            self.padding = tuple(
                (padding, padding) for _ in range(self.num_spatial_dims)
            )
        elif isinstance(padding, Sequence) and len(padding) == self.num_spatial_dims:
            self.padding = tuple((p, p) for p in padding)
        else:
            raise ValueError(
                "`padding` must either be an int or tuple of length "
                f"{self.num_spatial_dims}."
            )
        self.dilation = parse(dilation)

    def __call__(
        self, x: Array, *, key: Optional["jax.random.PRNGKey"] = None
    ) -> Array:
        """**Arguments:**

        - `x`: The input. Should be a JAX array of shape `(in_channels, dim_1, ..., dim_N)`, where
            `N = num_spatial_dims`.
        - `key`: Ignored; provided for compatibility with the rest of the Equinox API.
            (Keyword only argument.)

        **Returns:**

        A JAX array of shape `(out_channels, new_dim_1, ..., new_dim_N)`.
        """

        unbatched_rank = self.num_spatial_dims + 1
        if x.ndim != unbatched_rank:
            raise ValueError(
                f"Input to `Conv` needs to have rank {unbatched_rank},",
                f" but input has shape {x.shape}.",
            )
        x = jnp.expand_dims(x, axis=0)
        x = conv_general_dilated(
            lhs=x,
            rhs=self.weight,
            window_strides=self.stride,
            padding=self.padding,
            rhs_dilation=self.dilation,
        )
        if self.use_bias:
            x += jnp.broadcast_to(self.bias, x.shape)
        x = jnp.squeeze(x, axis=0)
        return x


class Conv1d(Conv):
    """As [`equinox.nn.Conv`][] with `num_spatial_dims=1`."""

    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size,
        stride=1,
        padding=0,
        dilation=1,
        use_bias=True,
        *,
        key,
        **kwargs,
    ):
        super().__init__(
            num_spatial_dims=1,
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            use_bias=use_bias,
            key=key,
            **kwargs,
        )


class Conv2d(Conv):
    """As [`equinox.nn.Conv`][] with `num_spatial_dims=2`."""

    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size,
        stride=(1, 1),
        padding=(0, 0),
        dilation=(1, 1),
        use_bias=True,
        *,
        key,
        **kwargs,
    ):
        super().__init__(
            num_spatial_dims=2,
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            use_bias=use_bias,
            key=key,
            **kwargs,
        )


class Conv3d(Conv):
    """As [`equinox.nn.Conv`][] with `num_spatial_dims=3`."""

    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size,
        stride=(1, 1, 1),
        padding=(0, 0, 0),
        dilation=(1, 1, 1),
        use_bias=True,
        *,
        key,
        **kwargs,
    ):
        super().__init__(
            num_spatial_dims=3,
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            use_bias=use_bias,
            key=key,
            **kwargs,
        )


class ConvTranspose(Module):
    """General N dimension Transpose Convolution"""

    num_spatial_dims: int = static_field()
    weight: Array
    bias: Optional[Array]
    in_channels: int = static_field()
    out_channels: int = static_field()
    kernel_size: Tuple[int] = static_field()
    stride: Tuple[int] = static_field()
    padding: Tuple[int] = static_field()
    output_padding: Tuple[int] = static_field()
    dilation: Tuple[int] = static_field()
    use_bias: bool = static_field()
    dimension_numbers: Tuple[str] = static_field()

    def __init__(
        self,
        num_spatial_dims: int,
        in_channels: int,
        out_channels: int,
        kernel_size: Union[int, Sequence[int]],
        stride: Union[int, Sequence[int]] = 1,
        padding: Union[int, Sequence[int]] = 0,
        output_padding: Union[int, Sequence[int]] = 0,
        dilation: Union[int, Sequence[int]] = 1,
        use_bias: bool = True,
        *,
        key: "jax.random.PRNGKey",
        **kwargs,
    ):
        """**Arguments:**

        - `num_spatial_dims`: The number of spatial dimensions. For example traditional
            convolutions for image processing have this set to `2`.
        - `in_channels`: The number of input channels.
        - `out_channels`: The number of output channels.
        - `kernel_size`: The size of the transposed convolutional kernel.
        - `stride`: The stride of the transposed convolution.
        - `padding`: The amount of implicit padding on both sides for `dilation *
            (kernel_size - 1) - padding` points.
        - `output_padding`: The additional size added to the output shape.
        - `dilation`: The spacing between kernel points.
        - `use_bias`: Whether to add on a bias after the transposed convolution.
        - `key`: A `jax.random.PRNGKey` used to provide randomness for parameter
            initialisation. (Keyword only argument.)

        !!! info

            All of `kernel_size`, `stride`, `padding`, `output_padding`, `dilation` can
            be either an integer or a sequence of integers. If they are a sequence then
            the sequence should be of length equal to `num_spatial_dims`, and specify
            the value of each property down each spatial dimension in turn.. If they
            are an integer then the same kernel size / stride / padding / dilation will
            be used along every spatial dimension.

        """
        super().__init__(**kwargs)
        self.num_spatial_dims = num_spatial_dims
        parse = _ntuple(self.num_spatial_dims)
        wkey, bkey = jrandom.split(key, 2)
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel_size = parse(kernel_size)
        self.use_bias = use_bias
        self.output_padding = parse(output_padding)
        self.padding = parse(padding)
        lim = 1 / np.sqrt(self.in_channels * np.prod(self.kernel_size))
        if self.num_spatial_dims == 1:
            self.dimension_numbers = ("NCH", "IOH", "NCH")
        elif self.num_spatial_dims == 2:
            self.dimension_numbers = ("NCHW", "IOHW", "NCHW")
        elif self.num_spatial_dims == 3:
            self.dimension_numbers = ("NCDHW", "IODHW", "NCDHW")
        else:
            raise NotImplementedError(
                "`ConvTranspose` only supports between 1 and 3 spatial dims",
                f"({self.num_spatial_dims} was given)",
            )
        self.weight = jrandom.uniform(
            wkey,
            (
                self.in_channels,
                self.out_channels,
            )
            + self.kernel_size,
            minval=-lim,
            maxval=lim,
        )
        if self.use_bias:
            self.bias = jrandom.uniform(
                bkey,
                (self.out_channels,) + (1,) * self.num_spatial_dims,
                minval=-lim,
                maxval=lim,
            )
        else:
            self.bias = None

        self.stride = parse(stride)
        self.dilation = parse(dilation)

    def __call__(
        self, x: Array, *, key: Optional["jax.random.PRNGKey"] = None
    ) -> Array:
        """**Arguments:**

        - `x`: The input. Should be a JAX array of shape `(in_channels, dim_1, ..., dim_N)`, where
            `N = num_spatial_dims`.
        - `key`: Ignored; provided for compatibility with the rest of the Equinox API.
            (Keyword only argument.)

        **Returns:**

        A JAX array of shape `(out_channels, new_dim_1, ..., new_dim_N)`.
        """
        unbatched_rank = self.num_spatial_dims + 1
        if x.ndim != unbatched_rank:
            raise ValueError(
                f"Input to `ConvTranspose` needs to have rank {unbatched_rank},",
                f" but input has shape {x.shape}.",
            )
        x = jnp.expand_dims(x, axis=0)
        padding = self.padding
        if self.output_padding is not None:
            padding = tuple(
                map(
                    compute_adjusted_padding,
                    x.shape[2:],
                    self.weight.shape[2:],
                    self.stride,
                    self.padding,
                    self.output_padding,
                    self.dilation,
                )
            )
        x = conv_transpose(
            lhs=x,
            rhs=self.weight,
            strides=self.stride,
            padding=padding,
            rhs_dilation=self.dilation,
            dimension_numbers=self.dimension_numbers,
        )
        if self.use_bias:
            x += jnp.broadcast_to(self.bias, x.shape)
        x = jnp.squeeze(x, axis=0)
        return x


class ConvTranspose1d(ConvTranspose):
    """As [`equinox.nn.ConvTranspose`][] with `num_spatial_dims=1`."""

    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size,
        stride=1,
        output_padding=0,
        padding=0,
        dilation=1,
        use_bias=True,
        *,
        key,
        **kwargs,
    ):
        super().__init__(
            num_spatial_dims=1,
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            stride=stride,
            output_padding=output_padding,
            padding=padding,
            dilation=dilation,
            use_bias=use_bias,
            key=key,
            **kwargs,
        )


class ConvTranspose2d(ConvTranspose):
    """As [`equinox.nn.ConvTranspose`][] with `num_spatial_dims=2`."""

    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size,
        stride=(1, 1),
        output_padding=(0, 0),
        padding=(0, 0),
        dilation=(1, 1),
        use_bias=True,
        *,
        key,
        **kwargs,
    ):
        super().__init__(
            num_spatial_dims=2,
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            stride=stride,
            output_padding=output_padding,
            padding=padding,
            dilation=dilation,
            use_bias=use_bias,
            key=key,
            **kwargs,
        )


class ConvTranspose3d(ConvTranspose):
    """As [`equinox.nn.ConvTranspose`][] with `num_spatial_dims=3`."""

    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size,
        stride=(1, 1, 1),
        output_padding=(0, 0, 0),
        padding=(0, 0, 0),
        dilation=(1, 1, 1),
        use_bias=True,
        *,
        key,
        **kwargs,
    ):
        super().__init__(
            num_spatial_dims=3,
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            stride=stride,
            output_padding=output_padding,
            padding=padding,
            dilation=dilation,
            use_bias=use_bias,
            key=key,
            **kwargs,
        )
