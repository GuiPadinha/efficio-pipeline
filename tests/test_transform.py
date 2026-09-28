import pandas as pd

from company_pipeline.transform import enrich_companies


def test_enrich_companies_attaches_parent_and_detail():
    # A made-up family of three: Mother owns Daughter, Daughter owns Granddaughter.
    # The DUNS start with zeros on purpose: they must survive as text.
    company = pd.DataFrame({
        "duns": ["000000001", "000000002", "000000003"],
        "name": ["Mother", "Daughter", "Granddaughter"],
        "parent_duns": [None, "000000001", "000000002"],
    }).astype("string")
    detail = pd.DataFrame({"duns": ["000000001"], "website": ["www.mother.com"]}).astype("string")

    result = enrich_companies(company, detail).set_index("duns")

    assert len(result) == 3                                     # no company added, dropped or duplicated
    assert result.loc["000000002", "parent_name"] == "Mother"
    assert result.loc["000000003", "parent_name"] == "Daughter"
    assert pd.isna(result.loc["000000001", "parent_name"])     # the top of the tree has no parent
    assert result.loc["000000001", "website"] == "www.mother.com"
    assert pd.isna(result.loc["000000003", "website"])         # detail exists only for the top
