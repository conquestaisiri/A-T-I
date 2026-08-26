# backend/application/sentiment/sentiment_service.py
"""GDELT + FinBERT sentiment service.

Fetches global news from GDELT (15-min updates, free), runs FinBERT
inference on headlines, and caches sentiment scores per symbol.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# FinBERT labels
_LABELS = ("negative", "neutral", "positive")
# Simple crypto symbol → search terms mapping
_SYMBOL_TERMS = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "eth"),
    "SOL": ("solana", "sol"),
    "BNB": ("binance", "bnb"),
    "XRP": ("ripple", "xrp"),
    "ADA": ("cardano", "ada"),
    "DOGE": ("dogecoin", "doge"),
    "MATIC": ("polygon", "matic"),
    "DOT": ("polkadot", "dot"),
    "AVAX": ("avalanche", "avax"),
}


class SentimentService:
    """Background service that fetches GDELT news and caches FinBERT sentiment."""

    def __init__(
        self,
        *,
        update_interval_seconds: int = 900,  # 15 min (GDELT update frequency)
        symbols: tuple[str, ...] = ("BTC", "ETH", "SOL", "BNB", "XRP"),
        model_name: str = "ProsusAI/finbert",
        device: str | None = None,
    ) -> None:
        self._update_interval = update_interval_seconds
        self._symbols = symbols
        self._cache: dict[str, dict[str, Any]] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Load FinBERT lazily (heavy deps optional per P0-001)
        logger.info("Loading FinBERT model: %s", model_name)
        import torch  # noqa: PLC0415
        from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: PLC0415

        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self._model.eval()
        if device is not None:
            self._device = device
        else:
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model.to(self._device)
        logger.info("FinBERT loaded on %s", self._device)

    @property
    def cache(self) -> dict[str, dict[str, Any]]:
        """Read-only view of the sentiment cache."""
        return dict(self._cache)

    def get_sentiment(self, symbol: str) -> dict[str, Any] | None:
        """Get latest sentiment for a symbol, or None if not available."""
        return self._cache.get(symbol.upper())

    async def start(self) -> None:
        """Start the background update loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("SentimentService started for %s", self._symbols)

    async def stop(self) -> None:
        """Stop the background update loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("SentimentService stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._update_all()
            except Exception as exc:
                logger.warning("Sentiment update failed: %s", exc)
            await asyncio.sleep(self._update_interval)

    async def _update_all(self) -> None:
        """Fetch GDELT news and update sentiment for all symbols."""
        articles = await self._fetch_gdelt_articles()
        if not articles:
            logger.debug("No GDELT articles fetched")
            return

        # Group articles by symbol
        symbol_articles: dict[str, list[str]] = defaultdict(list)
        for article in articles:
            title = article.get("title", "")
            for symbol, terms in _SYMBOL_TERMS.items():
                if any(term.lower() in title.lower() for term in terms):
                    symbol_articles[symbol].append(title)

        # Run FinBERT inference
        for symbol, titles in symbol_articles.items():
            if not titles:
                continue
            scores = self._infer_sentiment(titles)
            self._cache[symbol] = {
                "sentiment_score": scores["sentiment_score"],  # -1 to +1
                "positive": scores["positive"],
                "neutral": scores["neutral"],
                "negative": scores["negative"],
                "article_count": len(titles),
                "updated_at": datetime.now(UTC).isoformat(),
            }
            logger.debug(
                "Updated %s sentiment: %.3f (%d articles)",
                symbol,
                scores["sentiment_score"],
                len(titles),
            )

    async def _fetch_gdelt_articles(self) -> list[dict[str, Any]]:
        """Fetch recent articles from GDELT GKG (free, no API key)."""
        # GDELT 2.0 updates every 15 min; we query the last 30 min window
        now = datetime.now(UTC)
        start = (now - timedelta(minutes=30)).strftime("%Y%m%d%H%M%S")
        end = now.strftime("%Y%m%d%H%M%S")
        url = (
            "https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query=bitcoin+OR+ethereum+OR+crypto+OR+cryptocurrency"
            f"&mode=artlist&format=json&startdatetime={start}&enddatetime={end}"
            f"&maxrecords=250"
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                articles = data.get("articles", [])
                return articles if isinstance(articles, list) else []
        except Exception as exc:
            logger.warning("GDELT fetch failed: %s", exc)
            return []

    def _infer_sentiment(self, texts: list[str]) -> dict[str, float]:
        """Run FinBERT on a batch of texts, return averaged probabilities."""
        import torch  # noqa: PLC0415

        # Tokenize
        inputs = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        ).to(self._device)

        with torch.no_grad():
            outputs = self._model(**inputs)
            probs = torch.softmax(outputs.logits, dim=-1).cpu().numpy()

        # Average across texts
        mean_probs = probs.mean(axis=0)
        # Read label order from model config (don't hardcode)
        # FinBERT: id2label = {'0': 'positive', '1': 'negative', '2': 'neutral'}
        label_map = {v.lower(): int(k) for k, v in self._model.config.id2label.items()}
        pos_idx = label_map["positive"]
        neg_idx = label_map["negative"]
        neu_idx = label_map["neutral"]
        # Sentiment score: positive - negative (range -1 to +1)
        sentiment = float(mean_probs[pos_idx] - mean_probs[neg_idx])

        return {
            "sentiment_score": round(sentiment, 4),
            "positive": round(float(mean_probs[pos_idx]), 4),
            "neutral": round(float(mean_probs[neu_idx]), 4),
            "negative": round(float(mean_probs[neg_idx]), 4),
        }


def _demo_inference() -> None:
    """Quick demo for manual testing."""
    from transformers import pipeline

    pipe = pipeline("text-classification", model="ProsusAI/finbert")
    texts = [
        "Bitcoin surges to new all-time high on institutional adoption",
        "Ethereum crashes after SEC lawsuit announcement",
        "Market remains neutral as Fed holds rates steady",
    ]
    for text in texts:
        result = pipe(text)[0]
        print(f"{text[:60]}... → {result['label']} ({result['score']:.3f})")


if __name__ == "__main__":
    _demo_inference()
