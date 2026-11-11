"""Turns rows (source, conversation, last_user_text, last_response) into the
data object the dashboard template expects."""
import json
import re
from collections import Counter, defaultdict

GROUP_LABEL = {"fastag": "FASTag", "non_fastag": "Non-FASTag (Highway/Plaza)"}
OTHER_TYPES = ["fastagstatus", "complaintstatus", "followup", "unclear"]
OTHER_TOPIC = "Other/Unmatched"

# ---------------------------------------------------------------- masking
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_VEHICLE = re.compile(
    r"\b(?:[A-Za-z]{2}[ -]?\d{1,2}[ -]?[A-Za-z]{1,3}[ -]?\d{4}"
    r"|\d{2}[ -]?BH[ -]?\d{4}[ -]?[A-Za-z]{1,2})\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[ -]?|0)?[6-9]\d{9}(?!\d)")
_SPACE = re.compile(r"\s+")


def mask(text):
    text = _EMAIL.sub("[email]", text)
    text = _VEHICLE.sub("[vehicle no.]", text)
    text = _PHONE.sub("[phone]", text)
    return _SPACE.sub(" ", text).strip()


# ---------------------------------------------------------------- language
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_OTHER_INDIC = re.compile(r"[ঀ-෿]")
_HINGLISH = re.compile(
    r"\b(?:kaise|kese|kya|kyu|kyun|kyon|hai|hain|nahi|nhi|nahin|mera|meri"
    r"|mere|mujhe|mujhko|karna|karen|karein|karu|karun|karo|kare|krna|kr"
    r"|raha|rahi|rahe|gaya|gayi|gaye|hua|hui|hoga|hogi|hota|hoti|tha|thi"
    r"|chahiye|chahie|paisa|paise|rupaye|wapas|vapas|batao|bataye|bataiye"
    r"|kitna|kitne|kitni|kab|kaha|kahan|kahaan|abhi|gadi|gaadi|wala|wali"
    r"|liye|aur|bhi|hoon|hun|yeh|woh|aap|apna|apni|humara|hamara|sakta"
    r"|sakte|sakti|kat|kata|kaat|band|chalu|karwana|karvana|dikha|bhai"
    r"|ji|ka|ki|ko|se|mein|h)\b", re.I)


def language(text):
    if _DEVANAGARI.search(text):
        return "Hindi / Marathi (Devanagari)"
    if _OTHER_INDIC.search(text):
        return "Other Indic scripts"
    if _HINGLISH.search(text):
        return "Hinglish (Roman)"
    return "English"


# ---------------------------------------------------------------- FAQ topics
# First matching rule wins, so order matters (e.g. "annual pass porting" is
# Porting, "local annual pass" is Local Pass).
_FAQ_RULES = [(name, re.compile(pat, re.I)) for name, pat in [
    ("Porting", r"\bport(?:ing|ed|ability)?\b|transfer (?:my |the )?annual"),
    ("Local Pass", r"\blocal\b.*\bpass|\bpass\b.*\blocal\b"),
    ("Monthly Pass", r"\bmonthly\b|\bmonth pass"),
    ("Annual Pass", r"an+ua?l|yearly|\bpass(?:es)?\b|renew|re-?activat|200 trip"
                    r"|validity|(?:trips?|tolls?) (?:are )?(?:left|remaining)"
                    r"|3000|वार्षिक|पास"),
    ("e-Notice / e-Challan", r"\be[- ]?notice|\bnotice\b|ch?al+an|चालान|ચલણ"),
    ("Refund", r"refund|रिफंड"),
    ("Receipt/Invoice/Bill", r"receipt|reciept|recipt|invoice|\bbill\b"),
    ("Statement/History", r"stat\w*ment|hist\w*ry|transaction|passbook|trip details"),
    ("KYC/Documents", r"kyc\b|\bkyv\b|document|केवाईसी"),
    ("FASTag Hotlisted/Blacklisted", r"hot ?list|black ?list|\bunblock|\bblock"),
    ("FASTag Closure/Cancellation",
     r"\bclos(?:e|ed|ure|ing)\b|de-?activat|cancel|surrender|band kar"),
    ("Language / App Settings",
     r"lang\w*age|\benglish\b|\bhindi\b|bhasha|marathi|tamil|telugu|gujarati"
     r"|kannada|bengali|punjabi|malayalam|भाषा"),
    ("Vehicle/Tag Linking Change",
     r"mobile n|phone n|\bvrn\b|e-?mail|mail id|\blink|change.*\bbank"
     r"|(?:change|update|edit|correct).*(?:number|vehicle|name|address)"),
    ("Toll Exemption/Concession",
     r"exempt|concession|defen[cs]e|\barmy\b|handicap|ex[- ]?service|disabled"),
    ("Customer Care/Helpline",
     r"customer care|help ?line|toll[- ]?free|contact|complain|\b1033\b|\bcall\b"),
    ("FASTag Balance/Recharge", r"balance|recharge|top[- ]?up|wallet|रिचार्ज"),
    ("New FASTag Purchase/Activation",
     r"\bbuy\b|purchase|\bnew\b|activat|\bapply\b|kharid"),
    ("Toll Rates/Fare",
     r"\brates?\b|\bfare\b|charges?\b|price|how much|toll tax|\bfees?\b|kitna"),
    ("Route/Plaza Info",
     r"route|plaza|speed|petrol|pump|highway|express ?way|distance|\bmap\b"
     r"|\bnh ?\d|traffic"),
    ("App/RajmargYatra General", r"\bapp\b|rajmarg|option|login|\botp\b"),
    ("NHAI General", r"\bnhai\b|\bnpci\b|ihmcl"),
    ("FASTag General (what is)", r"fas+t? ?t+ag|फास्टैग"),
]]


def faq_topic(text):
    for name, rx in _FAQ_RULES:
        if rx.search(text):
            return name
    return None


_NO_ANSWER = re.compile(
    r"don'?t have (?:the |any )?information|do not have (?:the |any )?information"
    r"|could not resolve|couldn'?t resolve", re.I)


def _key(text):
    return text.lower()


class _Phrases:
    """Counts phrasings case-insensitively, remembers the commonest spelling."""

    def __init__(self):
        self.counts = Counter()
        self.forms = {}

    def add(self, text):
        k = _key(text)
        self.counts[k] += 1
        forms = self.forms.get(k)
        if forms is None:
            self.forms[k] = forms = Counter()
        if len(forms) < 8 or text in forms:
            forms[text] += 1

    def top(self, n):
        return [[self.forms[k].most_common(1)[0][0], c]
                for k, c in self.counts.most_common(n)]


class Aggregator:
    def __init__(self):
        self.conversations = 0
        self.user_messages = 0
        self.bad_rows = 0
        self.fallback_errors = 0
        self.faq_no_answer = 0
        self.types = Counter()
        self.sources = Counter()
        self.langs = Counter()
        self.turns = Counter()
        self.sub = Counter()
        self.subsub = Counter()
        self.faq_topics = Counter()
        self.q_sub = defaultdict(_Phrases)
        self.q_subsub = defaultdict(_Phrases)
        self.q_faq = defaultdict(_Phrases)
        self.q_other = defaultdict(_Phrases)
        self.faq_all = _Phrases()
        self.complaint_all = _Phrases()

    def add(self, source, conversation, last_user_text, last_response):
        self.conversations += 1
        self.sources[source or "(unknown)"] += 1

        user_texts = []
        try:
            conv = json.loads(conversation) if conversation else []
            for msg in conv:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    user_texts.append(str(msg.get("text") or ""))
        except (ValueError, TypeError):
            pass
        self.user_messages += len(user_texts)
        if user_texts:
            self.turns[str(min(len(user_texts), 6))] += 1

        try:
            resp = json.loads(last_response) if last_response else None
        except (ValueError, TypeError):
            resp = None
        if not isinstance(resp, dict) or not resp.get("type"):
            self.bad_rows += 1
            resp = {"type": "unclear"}
        rtype = str(resp["type"])
        self.types[rtype] += 1

        raw = last_user_text or (user_texts[-1] if user_texts else "")
        self.langs[language(raw)] += 1
        text = mask(raw) or "(empty)"

        if rtype in GROUP_LABEL:
            group = GROUP_LABEL[rtype]
            sub = resp.get("sub_type_category") or "(no sub-type)"
            subsub = resp.get("sub_sub_type_category") or "(no sub-sub-type)"
            self.sub[(group, sub)] += 1
            self.subsub[(group, subsub)] += 1
            self.q_sub[f"{group}||{sub}"].add(text)
            self.q_subsub[f"{group}||{subsub}"].add(text)
            self.complaint_all.add(text)
        elif rtype == "faq":
            topic = faq_topic(raw)
            if topic is None:
                # final message too vague ("yes", "how"): use earlier ones
                for earlier in reversed(user_texts[:-1]):
                    topic = faq_topic(earlier)
                    if topic:
                        break
            topic = topic or OTHER_TOPIC
            self.faq_topics[topic] += 1
            self.q_faq[topic].add(text)
            self.faq_all.add(text)
            if _NO_ANSWER.search(str(resp.get("faq_answer") or "")):
                self.faq_no_answer += 1
        elif rtype in OTHER_TYPES:
            self.q_other[rtype].add(text)
            if rtype == "unclear" and resp.get("fallback_message"):
                self.fallback_errors += 1

    def result(self):
        t = self.types
        type_counts = dict(t.most_common())
        return {
            "totals": {
                "conversations": self.conversations,
                "user_messages": self.user_messages,
                "complaints": t["fastag"] + t["non_fastag"],
                "faqs": t["faq"],
                "followups": t["followup"],
                "status_checks": t["fastagstatus"] + t["complaintstatus"],
                "unclear": t["unclear"],
                "fallback_errors": self.fallback_errors,
                "faq_no_answer": self.faq_no_answer,
            },
            "type_counts": type_counts,
            "final_type": type_counts,
            "complaint_group": {g: t[k] for k, g in GROUP_LABEL.items() if t[k]},
            "complaint_sub": [[g, s, c] for (g, s), c in self.sub.most_common()],
            "complaint_subsub": [[g, s, c]
                                 for (g, s), c in self.subsub.most_common()],
            "faq_topics": dict(self.faq_topics.most_common()),
            "faq_top_queries": self.faq_all.top(40),
            "top_complaint_queries": self.complaint_all.top(30),
            "followup_top": self.q_other["followup"].top(30),
            "langs": dict(self.langs.most_common()),
            "turn_dist": {k: self.turns[k] for k in "123456" if self.turns[k]},
            "sources": dict(self.sources.most_common()),
            "queries": {
                "complaint_subsub": {f"{g}||{s}": self.q_subsub[f"{g}||{s}"].top(150)
                                     for (g, s), _ in self.subsub.most_common()},
                "complaint_sub": {f"{g}||{s}": self.q_sub[f"{g}||{s}"].top(150)
                                  for (g, s), _ in self.sub.most_common()},
                "faq": {k: self.q_faq[k].top(150)
                        for k, _ in self.faq_topics.most_common()},
                "other": {k: self.q_other[k].top(200)
                          for k in OTHER_TYPES if t[k]},
            },
        }
