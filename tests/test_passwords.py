import pytest

from app.core.config import Settings
from app.modules.auth.errors import WeakPasswordError
from app.modules.auth.passwords import PasswordHasher, PasswordPolicy, common_passwords

STRONG = "correct horse battery staple"


async def test_hash_is_argon2id_with_configured_params() -> None:
    hasher = PasswordHasher(Settings())
    password_hash = await hasher.hash(STRONG)
    assert password_hash.startswith("$argon2id$v=19$m=1024,t=1,p=1$")
    assert STRONG not in password_hash


async def test_verify_and_needs_rehash() -> None:
    hasher = PasswordHasher(Settings())
    password_hash = await hasher.hash(STRONG)
    assert await hasher.verify(password_hash, STRONG) is True
    assert await hasher.verify(password_hash, STRONG + "!") is False
    assert await hasher.verify("not-a-hash", STRONG) is False
    assert hasher.needs_rehash(password_hash) is False
    stronger = PasswordHasher(Settings(argon2_time_cost=2))
    assert stronger.needs_rehash(password_hash) is True


def test_policy_rejects_too_short() -> None:
    with pytest.raises(WeakPasswordError) as exc_info:
        PasswordPolicy(min_length=12).check("Zq8!vR2#pL")
    assert exc_info.value.details == {"reasons": ["too_short"]}


def test_policy_accepts_strong_password() -> None:
    PasswordPolicy(min_length=12).check(STRONG)


def test_policy_rejects_common_password_case_insensitive() -> None:
    assert "password1234" in common_passwords()
    with pytest.raises(WeakPasswordError) as exc_info:
        PasswordPolicy(min_length=12).check("PASSWORD1234")
    assert exc_info.value.details == {"reasons": ["too_common"]}


def test_policy_counts_code_points_after_nfkc() -> None:
    policy = PasswordPolicy(min_length=12)
    # 12 accented letters / emoji are 12 code points (not bytes).
    policy.check("éàüöçñåøßæœ€")
    policy.check("🔐🦊🌵🎲🧩🚲🪐🍋🐙🎈🧭🌋")
    # Full-width digits normalise (NFKC) to ASCII and hit the common list.
    with pytest.raises(WeakPasswordError) as exc_info:
        policy.check("".join(chr(ord(c) + 0xFEE0) for c in "password1234"))
    assert exc_info.value.details == {"reasons": ["too_common"]}
    # Ligature "ﬀ" (1 code point) becomes "ff" (2): length is counted after NFKC.
    policy.check("ﬀ" * 6)
