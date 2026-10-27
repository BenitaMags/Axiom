"""
core/constraints.py
───────────────────
Deterministic cross-agent constraints derived from equivalence groups.
Resolver produces free-text pitfalls; these helpers turn them into
actionable flags and vetoes that downstream agents must respect.
"""

from __future__ import annotations

from smart_loader.core.state import EquivalenceGroup

# Packages that must never replace cryptographic hash usage.
NON_CRYPTO_HASH_PACKAGES = frozenset({"xxhash", "murmurhash", "farmhash", "mmh3"})

CRYPTO_API_TOKENS = frozenset({
    "sha256", "sha512", "sha384", "sha224", "sha1", "md5",
    "pbkdf2", "hmac", "scrypt", "blake2", "cryptographic",
})

CONNECTOR_PITFALL_MARKERS = (
    "different api", "different function", "returns bytes", "not str",
    "completely different", "connector needed", "no direct",
    "function names", "module structure differ", "incompatible",
)


def _normalize_api_token(api: str) -> str:
    return api.split(".")[-1].lower()


def infer_group_flags(
    group: EquivalenceGroup,
    llm_crypto: bool | None = None,
    llm_connector: bool | None = None,
) -> EquivalenceGroup:
    """Enrich an equivalence group with structured constraint flags."""
    pitfalls_text = " ".join(group.pitfalls).lower()
    reasoning_text = (group.reasoning or "").lower()
    combined_text = f"{pitfalls_text} {reasoning_text}"
    apis_text = " ".join(_normalize_api_token(a) for a in group.used_apis)

    crypto_required = bool(llm_crypto)
    if not crypto_required:
        crypto_required = any(tok in apis_text for tok in CRYPTO_API_TOKENS)

    # The resolver LLM may explicitly state that a hash usage is NOT for
    # security purposes (e.g. "hashlib.md5 used for non-crypto deduplication
    # checksums"). That's strong, specific evidence overriding the blunt
    # API-name match above: md5/sha256/etc. tokens are crypto APIs by
    # default, but their PRESENCE doesn't mean their USE in this particular
    # codebase is security-sensitive. Bug history: this used to set
    # crypto_required = True on these phrases, which is backwards — it
    # caused non-cryptographic hash usages (xxhash, the fast alternative)
    # to be vetoed FROM the role that explicitly doesn't need crypto
    # guarantees, defeating the entire point of the flag.
    explicit_non_crypto_phrases = (
        "not cryptographically",
        "non-cryptographic",
        "not crypto",
        "non-crypto",
        "explicitly noted as non-crypto",
    )
    explicit_crypto_phrases = (
        "security-sensitive hashing",
        "security purpose",
        "cryptographically secure",
        "for security",
    )
    if any(phrase in combined_text for phrase in explicit_non_crypto_phrases):
        crypto_required = False
    elif any(phrase in combined_text for phrase in explicit_crypto_phrases):
        crypto_required = True

    requires_connector = bool(llm_connector)
    if not requires_connector:
        requires_connector = any(m in pitfalls_text for m in CONNECTOR_PITFALL_MARKERS)
    if crypto_required and any(c in NON_CRYPTO_HASH_PACKAGES for c in group.candidates):
        requires_connector = True

    group.crypto_required = crypto_required
    group.requires_connector = requires_connector
    return group


def filter_crypto_candidates(group: EquivalenceGroup, candidates: list[str]) -> list[str]:
    """Remove non-cryptographic hash packages when crypto is required."""
    if not group.crypto_required:
        return candidates
    filtered = [c for c in candidates if c not in NON_CRYPTO_HASH_PACKAGES]
    return filtered or candidates[:1]
