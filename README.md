# news

A daily news digest that pulls from RSS feeds, summarizes each article in English and Chinese using Gemini, and publishes a static page via Cloudflare Pages.

Runs automatically at 15:00 Beijing time every day via GitHub Actions.

## How it works

1. `fetch.py` pulls up to 5 articles from each feed (40 total)
2. All articles are sent to Gemini in a single prompt — each gets a 2-sentence EN summary, a 2-sentence ZH summary, and a category label (AI / Technology / World / Science / Finance)
3. The script generates `index.html` and saves an archive snapshot to `archive/YYYY-MM-DD.html`
4. GitHub Actions commits the output and pushes; Cloudflare Pages deploys automatically

## Feeds

少数派, V2EX, Hacker News, OpenAI Blog, AI News, BBC, The Verge, MIT Tech Review, 36氪, MarketWatch, Seeking Alpha, GitHub Blog

## Setup

**Secret required (GitHub → Settings → Secrets):**

- `GEMINI_API_KEY` — Google AI Studio key with Gemini API access

**Run locally:**

```sh
pip install -r requirements.txt
GEMINI_API_KEY=your_key python fetch.py
```

## Deployment

Connected to Cloudflare Pages — any push to `main` deploys automatically. To trigger a manual run: GitHub → Actions → Daily News → Run workflow.
