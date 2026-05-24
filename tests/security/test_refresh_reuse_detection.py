"""Sec-E / KAN-98 — refresh-token reuse detection (audit finding #19).

Refresh-token rotation invalidates the prior token, so a stolen-then-used
token will be rejected when the original holder rotates next. The audit's
recommendation goes one step further: when a *consumed* token is replayed,
revoke the entire token family. That converts a covert theft into a noisy
boot-out — the attacker holding the rotated token can no longer use it
either, and the legitimate user is forced to re-authenticate (visible in
the audit log).

Design constraints we pin here:

* Reuse of a consumed refresh token returns ``None`` (the existing
  contract — the token endpoint translates that to ``invalid_grant``).
* The reuse ALSO revokes every refresh token in the same family,
  including the freshly rotated one in the legitimate user's hands.
* Sibling families are unaffected — only the offending family is burnt.
* The rotation chain is preserved across multiple successful refreshes
  (token A → B → C → D, then a replay of A burns D too).
"""

from __future__ import annotations

from vividscripts_mcp.oauth.tokens import (
    MockRefreshTokenStore,
    mint_refresh_token,
)

# ---------------------------------------------------------------------
# Reuse-detection contract
# ---------------------------------------------------------------------


def test_consumed_refresh_token_replay_returns_none() -> None:
    """Baseline: the first consume returns the record, second returns None."""
    store = MockRefreshTokenStore()
    token, record = mint_refresh_token(user_id="user-alpha", client_id="c")
    store.add(record)

    first = store.consume(token)
    second = store.consume(token)

    assert first is not None
    assert second is None


def test_consumed_token_replay_revokes_freshly_rotated_token() -> None:
    """Replaying token A burns the legitimate rotated token B in the same family."""
    store = MockRefreshTokenStore()
    token_a, record_a = mint_refresh_token(user_id="user-alpha", client_id="c")
    store.add(record_a)

    # Consume A — simulates a legitimate refresh.
    consumed_a = store.consume(token_a)
    assert consumed_a is not None

    # Rotate: mint B in the same family.
    token_b, record_b = mint_refresh_token(
        user_id="user-alpha",
        client_id="c",
        family_id=consumed_a.family_id,
    )
    store.add(record_b)

    # Replay A — the attacker's path.
    replay = store.consume(token_a)
    assert replay is None

    # B is now ALSO revoked — the family was burnt by the reuse.
    legitimate_use_of_b = store.consume(token_b)
    assert legitimate_use_of_b is None, (
        "audit finding #19: replay of a consumed token must revoke the entire family"
    )


def test_long_chain_reuse_revokes_latest_token() -> None:
    """A → B → C → D: replay of A burns D too (transitive family revocation)."""
    store = MockRefreshTokenStore()
    tokens: list[str] = []
    last_family: str | None = None

    # Mint A and consume it three times (chain length 4: A, B, C, D).
    token, record = mint_refresh_token(user_id="user-alpha", client_id="c")
    store.add(record)
    tokens.append(token)
    last_family = record.family_id

    for _ in range(3):
        consumed = store.consume(tokens[-1])
        assert consumed is not None
        next_token, next_record = mint_refresh_token(
            user_id="user-alpha",
            client_id="c",
            family_id=consumed.family_id,
        )
        store.add(next_record)
        tokens.append(next_token)
        last_family = next_record.family_id

    # Now replay the *first* token (A).
    replay = store.consume(tokens[0])
    assert replay is None

    # The latest token (D) must be revoked too.
    legitimate_use_of_d = store.consume(tokens[-1])
    assert legitimate_use_of_d is None, (
        f"family {last_family!r} should be fully revoked after reuse of the head"
    )


def test_sibling_family_not_affected_by_unrelated_reuse() -> None:
    """Reusing one token does not affect tokens minted in a different family."""
    store = MockRefreshTokenStore()

    # Family X
    token_x, record_x = mint_refresh_token(user_id="user-alpha", client_id="c")
    store.add(record_x)

    # Family Y
    token_y, record_y = mint_refresh_token(user_id="user-beta", client_id="c")
    store.add(record_y)

    # Different families produced by separate mint calls.
    assert record_x.family_id != record_y.family_id

    # Replay token X (after consume to mark it tombstoned).
    consumed_x = store.consume(token_x)
    assert consumed_x is not None
    replay = store.consume(token_x)
    assert replay is None

    # Y is still good.
    consumed_y = store.consume(token_y)
    assert consumed_y is not None
    assert consumed_y.user_id == "user-beta"


def test_expired_token_not_tombstoned_for_family_revocation() -> None:
    """An expired token's natural rejection does not burn its family.

    Reuse-detection is specifically about *replaying a previously
    consumed* token. Letting a token expire is not a reuse signal —
    burning the family on expiry would generate a denial-of-service
    every time a refresh window lapsed.
    """
    store = MockRefreshTokenStore()
    # Negative ttl deliberately places expires_at in the past. Avoids
    # the ``int(now.timestamp())`` second-truncation race that ttl=0
    # introduces (consume()'s ``<`` check needs strictly-less, not
    # less-or-equal).
    token, record = mint_refresh_token(user_id="user-alpha", client_id="c", ttl_seconds=-10)
    store.add(record)

    # Consume returns None (expired). Family is NOT tombstoned.
    expired = store.consume(token)
    assert expired is None

    # Mint a fresh token in the same family (e.g. a legitimate reissue) —
    # it must still be usable.
    fresh_token, fresh_record = mint_refresh_token(
        user_id="user-alpha", client_id="c", family_id=record.family_id
    )
    store.add(fresh_record)
    consumed = store.consume(fresh_token)
    assert consumed is not None


def test_mint_refresh_token_assigns_family_id_when_not_provided() -> None:
    """Two independent mints get two independent families by default."""
    _, record_a = mint_refresh_token(user_id="user-alpha", client_id="c")
    _, record_b = mint_refresh_token(user_id="user-alpha", client_id="c")
    assert record_a.family_id != record_b.family_id


def test_mint_refresh_token_threads_explicit_family_id() -> None:
    """Rotation chains pass the family_id through explicitly."""
    _, record_a = mint_refresh_token(user_id="user-alpha", client_id="c")
    _, record_b = mint_refresh_token(
        user_id="user-alpha", client_id="c", family_id=record_a.family_id
    )
    assert record_b.family_id == record_a.family_id
