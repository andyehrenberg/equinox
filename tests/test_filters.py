import warnings

import jax
import jax.numpy as jnp
import numpy as np
import pytest

import equinox as eqx


def test_is_array(getkey):
    objs = [
        1,
        2.0,
        [2.0],
        True,
        object(),
        jnp.array([1]),
        jnp.array(1.0),
        np.array(1.0),
        np.array(1),
        eqx.nn.Linear(1, 1, key=getkey()),
    ]
    results = [False, False, False, False, False, True, True, False, False, False]
    for o, r in zip(objs, results):
        assert eqx.is_array(o) == r


def test_is_array_like(getkey):
    objs = [
        1,
        2.0,
        [2.0],
        True,
        object(),
        jnp.array([1]),
        jnp.array(1.0),
        np.array(1.0),
        np.array(1),
        eqx.nn.Linear(1, 1, key=getkey()),
    ]
    results = [True, True, False, True, False, True, True, True, True, False]
    for o, r in zip(objs, results):
        assert eqx.is_array_like(o) == r


def test_is_inexact_array(getkey):
    objs = [
        1,
        2.0,
        [2.0],
        True,
        object(),
        jnp.array([1]),
        jnp.array(1.0),
        np.array(1.0),
        np.array(1),
        eqx.nn.Linear(1, 1, key=getkey()),
    ]
    results = [False, False, False, False, False, False, True, False, False, False]
    for o, r in zip(objs, results):
        assert eqx.is_inexact_array(o) == r


def test_is_inexact_array_like(getkey):
    objs = [
        1,
        2.0,
        [2.0],
        True,
        object(),
        jnp.array([1]),
        jnp.array(1.0),
        np.array(1.0),
        np.array(1),
        eqx.nn.Linear(1, 1, key=getkey()),
    ]
    results = [False, True, False, False, False, False, True, True, False, False]
    for o, r in zip(objs, results):
        assert eqx.is_inexact_array_like(o) == r


def test_filter(getkey):
    filter_fn = lambda x: isinstance(x, int)
    for pytree in (
        [
            1,
            2,
            [
                3,
                "hi",
                {"a": jnp.array(1), "b": 4, "c": eqx.nn.MLP(2, 2, 2, 2, key=getkey())},
            ],
        ],
        [1, 1, 1, 1, "hi"],
    ):
        filtered = eqx.filter(pytree, filter_spec=filter_fn)
        for arg in jax.tree_leaves(filtered):
            assert isinstance(arg, int)
        num_int_leaves = sum(
            1 for leaf in jax.tree_leaves(filtered) if isinstance(leaf, int)
        )
        assert len(jax.tree_leaves(filtered)) == num_int_leaves

    filter_spec = [False, True, [filter_fn, True]]
    sentinel = object()
    pytree = [
        eqx.nn.Linear(1, 1, key=getkey()),
        eqx.nn.Linear(1, 1, key=getkey()),
        [eqx.nn.Linear(1, 1, key=getkey()), sentinel],
    ]
    filtered = eqx.filter(pytree, filter_spec=filter_spec)
    none_linear = jax.tree_map(lambda _: None, eqx.nn.Linear(1, 1, key=getkey()))
    assert filtered[0] is None
    assert filtered[1] == pytree[1]
    assert filtered[2][0] == none_linear
    assert filtered[2][1] is sentinel

    with pytest.raises(ValueError):
        eqx.filter(pytree, filter_spec=filter_spec[1:])


def test_partition_and_combine(getkey):
    filter_fn = lambda x: isinstance(x, int)
    for pytree in (
        [
            1,
            2,
            [
                3,
                "hi",
                {"a": jnp.array(1), "b": 4, "c": eqx.nn.MLP(2, 2, 2, 2, key=getkey())},
            ],
        ],
        [1, 1, 1, 1, "hi"],
    ):
        filtered, unfiltered = eqx.partition(pytree, filter_spec=filter_fn)
        for arg in jax.tree_leaves(filtered):
            assert isinstance(arg, int)
        for arg in jax.tree_leaves(unfiltered):
            assert not isinstance(arg, int)
        assert eqx.combine(filtered, unfiltered) == pytree
        assert eqx.combine(unfiltered, filtered) == pytree


def test_splitfn_and_merge(getkey):
    filter_fn = lambda x: isinstance(x, int)
    for pytree in (
        [
            1,
            2,
            [
                3,
                "hi",
                {"a": jnp.array(1), "b": 4, "c": eqx.nn.MLP(2, 2, 2, 2, key=getkey())},
            ],
        ],
        [1, 1, 1, 1, "hi"],
    ):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            int_args, notint_args, which, treedef = eqx.split(
                pytree, filter_fn=filter_fn
            )
        for arg in int_args:
            assert isinstance(arg, int)
        for arg in notint_args:
            assert not isinstance(arg, int)
        assert sum(which) == 4
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            re_pytree = eqx.merge(int_args, notint_args, which, treedef)
        assert re_pytree == pytree


def test_splittree_and_merge(getkey):
    linear = eqx.nn.Linear(1, 1, key=getkey())
    linear_tree = jax.tree_map(lambda _: True, linear)
    filter_tree = [
        True,
        False,
        [False, False, {"a": True, "b": False, "c": linear_tree}],
    ]
    for i, pytree in enumerate(
        (
            [1, 2, [3, True, {"a": jnp.array(1), "b": 4, "c": linear}]],
            [1, 1, [1, 1, {"a": 1, "b": 1, "c": linear}]],
        )
    ):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            keep_args, notkeep_args, which, treedef = eqx.split(
                pytree, filter_tree=filter_tree
            )
        if i == 0:
            assert set(notkeep_args) == {2, 3, True, 4}
        else:
            assert notkeep_args == [1, 1, 1, 1]
        assert sum(which) == 4

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            re_pytree = eqx.merge(keep_args, notkeep_args, which, treedef)
        assert re_pytree == pytree

    filter_tree = [True, [False, False]]
    pytree = [True, None]
    with pytest.raises(ValueError), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        eqx.split(pytree, filter_tree=filter_tree)
