.PHONY: install test lint sample-run

install:
	uv sync
	uv run playwright install chromium

test:
	uv run pytest

lint:
	uv run ruff check .

# Validate the pipeline end-to-end on a small Netflix TechBlog sample,
# per docs/plan.md "Success Criteria Before Scaling Up". Requires OPENROUTER_API_KEY.
sample-run:
	uv run pyro run-all \
		--company-name Netflix \
		--sitemap-url https://netflixtechblog.com/sitemap/sitemap.xml \
		--db data/netflix.db \
		--out architecture.md \
		--limit 10
