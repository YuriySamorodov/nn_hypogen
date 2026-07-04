from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TextVectorIndex:
    texts: list[str]
    vectorizer: object | None
    matrix: object | None

    @classmethod
    def build(cls, texts: list[str]) -> "TextVectorIndex":
        if not texts:
            return cls([], None, None)
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer

            vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=20000)
            matrix = vectorizer.fit_transform(texts)
            return cls(texts, vectorizer, matrix)
        except Exception:
            return cls(texts, None, None)

    def search(self, query: str, top_k: int = 8) -> list[tuple[int, float]]:
        if not self.texts:
            return []
        if self.vectorizer is None or self.matrix is None:
            return _keyword_scores(self.texts, query, top_k)
        from sklearn.metrics.pairwise import cosine_similarity

        query_vec = self.vectorizer.transform([query])
        scores = cosine_similarity(query_vec, self.matrix).ravel()
        order = np.argsort(scores)[::-1][:top_k]
        return [(int(i), float(scores[i])) for i in order if scores[i] > 0]


def _keyword_scores(texts: list[str], query: str, top_k: int) -> list[tuple[int, float]]:
    terms = [term.lower() for term in query.split() if len(term) > 2]
    scored = []
    for idx, text in enumerate(texts):
        low = text.lower()
        hits = sum(low.count(term) for term in terms)
        if hits:
            scored.append((idx, min(1.0, hits / max(1, len(terms)))))
    return sorted(scored, key=lambda item: item[1], reverse=True)[:top_k]

