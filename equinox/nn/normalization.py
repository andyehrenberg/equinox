from typing import Optional, Sequence, Union

import jax
import jax.numpy as jnp

from ..custom_types import Array
from ..module import Module, static_field


class LayerNorm(Module):
    """Layer Normalization as described in https://arxiv.org/abs/1607.06450"""

    normalized_shape: Union[int, Sequence[int]] = static_field()
    eps: float = static_field()
    elementwise_affine: bool = static_field()
    weight: Array
    bias: Array

    def __init__(
        self,
        normalized_shape: Union[int, Sequence[int]],
        eps: float = 1e-5,
        elementwise_affine: bool = True,
        *,
        key: "jax.random.PRNGKey",
        **kwargs,
    ):
        """**Arguments:**
        - `normalized_shape`: Input shape.
        - `eps`: Value added to denominator for numerical stability. Default: `1e-5`.
        - `elementwise_affine`: Whether the module has learnable affine parameters. Default: `True`.
        - `key`: Ignored; provided for compatibility with the rest of the Equinox API.
            (Keyword only argument.)
        """
        super().__init__(**kwargs)
        self.normalized_shape = normalized_shape
        self.eps = eps
        self.elementwise_affine = elementwise_affine
        self.weight = jnp.ones(self.normalized_shape) if elementwise_affine else None
        self.bias = jnp.zeros(self.normalized_shape) if elementwise_affine else None

    def __call__(
        self, x: Array, *, key: Optional["jax.random.PRNGKey"] = None
    ) -> Array:
        """**Arguments:**

        - `x`: A JAX array of shape `normalized_shape`.
        - `key`: Ignored; provided for compatibility with the rest of the Equinox API.
            (Keyword only argument.)

        **Returns:**

        A JAX array of shape `normalized_shape`.
        """
        mean = jnp.mean(x, keepdims=True)
        variance = jnp.var(x, keepdims=True)
        inv = jax.lax.rsqrt(variance + self.eps)
        out = (x - mean) * inv
        if self.elementwise_affine:
            out = self.weight * out + self.bias
        return out
