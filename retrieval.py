"""
retrieval.py — find the RELEVANT meetings / logs / learnings for a given task or goal
WITHOUT calling the AI.

Why this exists:
  The app used to inject up to 40 learnings (and lean on the model to find what's
  relevant) into prompts. That's expensive (tokens for text the model mostly ignores)
  and slow. Finding "what's relevant to this task right now" is a SEARCH problem, not a
  reasoning problem — so we do it here in plain Python (keyword overlap + topic match +
  recency), in microseconds, for free. Then only the few relevant snippets are sent to
  the model (or surfaced directly, with no AI call at all).

Everything here is dependency-light (pandas only) and deterministic.
"""

import re

# Small stoplist so scoring keys on meaningful words, not glue words.
_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "with", "at", "by",
    "is", "are", "be", "do", "did", "this", "that", "these", "those", "it", "as", "from",
    "your", "you", "i", "we", "my", "our", "me", "us", "them", "their", "they", "he",
    "she", "his", "her", "will", "can", "should", "would", "could", "have", "has", "had",
    "get", "got", "make", "made", "up", "out", "off", "so", "if", "then", "than", "but",
    "about", "into", "over", "after", "before", "more", "most", "some", "any", "all",
    "no", "not", "new", "old", "today", "day", "task", "tasks", "call", "calls",
}


def _tokens(text):
    """Lowercase meaningful word tokens of a string (set), stopwords removed."""
    if not text:
        return set()
    words = re.findall(r"[a-zA-Z0-9]+", str(text).lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


def _overlap_score(query_toks, text_toks):
    """How many meaningful query words appear in the item, normalised a little by the
    item's length so a huge blob doesn't always win."""
    if not query_toks or not text_toks:
        return 0.0
    shared = query_toks & text_toks
    if not shared:
        return 0.0
    # weight shared words; mild normalisation by sqrt of item size
    import math
    return len(shared) / math.sqrt(len(text_toks) + 1)


def _recency_rank(values):
    """Map a list of date/timestamp strings to a 0..1 recency weight by ORDER (newest=1).
    String compare works because dates are ISO ('YYYY-MM-DD' / 'YYYY-MM-DD HH:MM:SS')."""
    order = sorted(set(v for v in values if str(v).strip()))
    if not order:
        return {}
    pos = {v: i for i, v in enumerate(order)}
    n = max(1, len(order) - 1)
    return {v: (pos[v] / n) for v in order}   # oldest 0 .. newest 1


def top_relevant(df, query, text_cols, topic_col=None, date_col=None, k=5,
                 min_score=0.01, recency_weight=0.35, topic_bonus=0.6):
    """Return up to k rows of `df` most relevant to `query`, as a list of dict records.

    Scoring (all local, no AI):
      * keyword overlap between the query and the row's text columns
      * a bonus if the query mentions the row's topic (or vice-versa)
      * a recency nudge (newer rows rank a little higher) when date_col is given
    If the query has no usable keywords, falls back to most-recent rows.
    """
    if df is None or len(df) == 0:
        return []
    qtoks = _tokens(query)

    # precompute recency weights
    rec = {}
    if date_col and date_col in df.columns:
        rec = _recency_rank(df[date_col].tolist())

    scored = []
    for _, row in df.iterrows():
        # combined item text
        parts = []
        for c in text_cols:
            if c in row and str(row[c]).strip():
                parts.append(str(row[c]))
        ttoks = _tokens(" ".join(parts))
        content = _overlap_score(qtoks, ttoks)

        # topic match bonus (counts as content signal)
        if topic_col and topic_col in row:
            tp = str(row[topic_col] or "").strip().lower()
            if tp:
                tp_toks = _tokens(tp)
                if (tp and tp in str(query).lower()) or (qtoks & tp_toks):
                    content += topic_bonus

        # recency only nudges ORDER — it must not promote a zero-content item when the
        # caller gave a real query (that's how irrelevant-but-recent rows leak in).
        rec_boost = recency_weight * rec.get(row[date_col], 0.0) if rec else 0.0
        s = content + rec_boost
        scored.append((s, content, row))

    # if the query had real keywords, keep only rows with actual content signal; otherwise
    # (empty/keyword-less query) fall back to most-recent rows.
    if qtoks:
        hits = [(s, r) for s, content, r in scored if content > min_score]
        hits.sort(key=lambda x: x[0], reverse=True)
        return [r.to_dict() for _, r in hits[:k]]
    else:
        scored.sort(key=lambda x: rec.get(x[2].get(date_col), 0.0) if rec else 0.0,
                    reverse=True)
        return [r.to_dict() for _, _, r in scored[:k]]


# ---------------------------------------------------------------- convenience builders
# These take the DataFrames the app already has and return a SHORT plain-text context
# block to hand to the model (or to surface directly). Caller decides whether to use AI.

def relevant_learnings(learnings_df, query, k=4):
    """The few accepted learnings most relevant to a task/goal. Returns list of dicts."""
    return top_relevant(
        learnings_df, query, text_cols=["text"], topic_col="topic",
        date_col="created_at", k=k, recency_weight=0.25)


def relevant_meetings(meetings_df, query, k=3):
    """Recent meetings whose content relates to a task/goal/partner. Returns list of dicts."""
    return top_relevant(
        meetings_df, query, text_cols=["partner_name", "ai_written", "raw_dictation",
                                       "discussed", "next_action"],
        date_col="date", k=k, recency_weight=0.4)


def relevant_logs(logs_df, query, k=3):
    """Recent daily-log entries related to a task/goal. Returns list of dicts."""
    return top_relevant(
        logs_df, query, text_cols=["transcript", "partner_name"],
        date_col="date", k=k, recency_weight=0.4)


def _clip(s, n):
    s = str(s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[:n].rstrip() + "…"


def context_for_task(task_title, day_goal="", learnings_df=None, meetings_df=None,
                     logs_df=None, max_learnings=3, max_meetings=2, max_logs=1):
    """Build ONE compact, relevant context string for a task — selected in Python, so the
    AI prompt stays tiny. Empty string if nothing relevant (then the caller can skip AI).
    """
    query = f"{task_title} {day_goal}".strip()
    blocks = []

    if learnings_df is not None and len(learnings_df):
        ls = relevant_learnings(learnings_df, query, k=max_learnings)
        if ls:
            lines = [f"- {_clip(l.get('text'), 160)}" for l in ls]
            blocks.append("Relevant lessons this person accepted:\n" + "\n".join(lines))

    if meetings_df is not None and len(meetings_df):
        ms = relevant_meetings(meetings_df, query, k=max_meetings)
        if ms:
            lines = []
            for m in ms:
                who = _clip(m.get("partner_name") or m.get("identity_value"), 40)
                what = _clip(m.get("ai_written") or m.get("raw_dictation") or m.get("discussed"), 140)
                lines.append(f"- {who}: {what}" if who else f"- {what}")
            blocks.append("Relevant recent meetings:\n" + "\n".join(lines))

    if logs_df is not None and len(logs_df):
        gs = relevant_logs(logs_df, query, k=max_logs)
        if gs:
            lines = [f"- {_clip(g.get('transcript'), 160)}" for g in gs]
            blocks.append("Relevant recent log notes:\n" + "\n".join(lines))

    return "\n\n".join(blocks).strip()
