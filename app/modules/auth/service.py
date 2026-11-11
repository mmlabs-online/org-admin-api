from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.errors import EmailTakenError, TurnstileFailedError
from app.modules.auth.hooks import SignupHook
from app.modules.auth.passwords import PasswordHasher, PasswordPolicy
from app.modules.auth.schemas import SignupRequest
from app.modules.auth.turnstile import TurnstileVerifier
from app.modules.users.models import User
from app.modules.users.repository import UserRepository


class SignupService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: PasswordPolicy,
        hasher: PasswordHasher,
        verifier: TurnstileVerifier,
        hook: SignupHook,
    ) -> None:
        self.session = session
        self.repo = UserRepository(session)
        self.policy = policy
        self.hasher = hasher
        self.verifier = verifier
        self.hook = hook

    async def signup(self, data: SignupRequest, remote_ip: str) -> User:
        email = str(data.email).strip().lower()
        password = data.password.get_secret_value()
        self.policy.check(password)
        if not await self.verifier.verify(data.turnstile_token, remote_ip):
            raise TurnstileFailedError("Verification failed")
        # Hash before touching the DB so 201 and 409 take similar time.
        password_hash = await self.hasher.hash(password)
        try:
            user = await self.repo.add(User(email=email, password_hash=password_hash))
        except IntegrityError as exc:  # unique email (case-insensitive via citext)
            raise EmailTakenError("Email is already registered") from exc
        await self.hook(user.id, self.session)
        return user
