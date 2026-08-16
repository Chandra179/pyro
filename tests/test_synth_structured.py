from pyro.db import Article
from pyro.synth.structured import _group_by_domain


def _article(domain: str | None) -> Article:
    return Article(
        id=domain or "none",
        source_url="u",
        company_name="c",
        extracted_facts={"domain": domain} if domain else {},
    )


def test_group_by_domain_defaults_missing_domain_to_other():
    articles = [_article("Authentication"), _article("Authentication"), _article(None)]
    groups = _group_by_domain(articles)
    assert set(groups) == {"Authentication", "Other"}
    assert len(groups["Authentication"]) == 2
    assert len(groups["Other"]) == 1
