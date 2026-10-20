# SPDX-License-Identifier: MIT
"""Skeleton tests: store, review tools, server assembly."""

from __future__ import annotations

import pytest
from citeproof_mcp import store

from tests.conftest import call


@pytest.mark.asyncio
async def test_review_create_and_list():
    created = await call("review_create", title="T", question="Q?", year_min=2010, year_max=2020)
    assert created["ok"] is True
    listed = await call("review_list")
    assert [r["review_id"] for r in listed["reviews"]] == [created["review_id"]]
    assert listed["reviews"][0]["year_max"] == 2020


@pytest.mark.asyncio
async def test_review_create_rejects_bad_input():
    assert (await call("review_create", title=" ", question="Q"))["ok"] is False
    assert (await call("review_create", title="T", question="Q", year_min=2020, year_max=2010))["ok"] is False


def test_review_id_is_validated_before_it_becomes_a_path():
    for bad in ("../x", "a/b", "ab", "UPPERCASE1", "x" * 41):
        with pytest.raises(store.StoreError):
            store.open_review(bad)


def test_unknown_review_raises():
    with pytest.raises(store.StoreError):
        store.open_review("rev-00000000")


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"doi": "https://doi.org/10.1234/ABC.5"}, "10.1234/abc.5"),
        ({"doi": "doi: 10.1234/abc"}, "10.1234/abc"),
        ({"pmid": "123"}, "pmid:123"),
        ({"title": "A  Title, Here!", "year": 2020}, "title:a title here|2020"),
        ({"title": "No Year"}, "title:no year|nd"),
    ],
)
def test_normalize_paper_ref(kwargs, expected):
    assert store.normalize_paper_ref(**kwargs) == expected


def test_normalize_paper_ref_needs_something():
    with pytest.raises(store.StoreError):
        store.normalize_paper_ref()
