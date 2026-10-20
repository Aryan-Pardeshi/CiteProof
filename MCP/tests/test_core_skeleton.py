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


@pytest.mark.asyncio
async def test_review_list_query_matches_title():
    await call("review_create", title="Asthma outcomes", question="Q1")
    diabetes = await call("review_create", title="Diabetes outcomes", question="Q2")

    listed = await call("review_list", query="diabetes")
    assert [r["review_id"] for r in listed["reviews"]] == [diabetes["review_id"]]


@pytest.mark.asyncio
async def test_review_list_query_matches_question():
    target = await call("review_create", title="Cardiology", question="How does diabetes affect outcomes?")
    await call("review_create", title="Oncology", question="What improves survival?")

    listed = await call("review_list", query="affect outcomes")
    assert [r["review_id"] for r in listed["reviews"]] == [target["review_id"]]


@pytest.mark.asyncio
async def test_review_list_query_is_case_insensitive():
    target = await call("review_create", title="Metabolic Health", question="Type 2 diabetes prevalence")

    listed = await call("review_list", query="DIABETES")
    assert [r["review_id"] for r in listed["reviews"]] == [target["review_id"]]


@pytest.mark.asyncio
async def test_review_list_query_no_matches():
    await call("review_create", title="Hypertension", question="Q")
    listed = await call("review_list", query="diabetes")
    assert listed["reviews"] == []
    assert listed["count"] == 0


@pytest.mark.asyncio
async def test_review_list_applies_limit_after_query_filtering():
    expected = await call("review_create", title="Diabetes care pathways", question="Q1")
    await call("review_create", title="Cardiology methods", question="Q2")

    listed = await call("review_list", limit=1, query="diabetes")
    assert [r["review_id"] for r in listed["reviews"]] == [expected["review_id"]]
    assert listed["count"] == 1


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
