import asyncio

from app.modules.email.sender import OutgoingEmail


class FakeTurnstileVerifier:
    """Accepts the token "pass"; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def verify(self, token: str, remote_ip: str) -> bool:
        self.calls.append((token, remote_ip))
        return token == "pass"


class FakeEmailSender:
    """Records sent messages. `failures` are raised in order, one per call, before sending;
    `gate`, when set, makes `send` wait until it is set."""

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []
        self.calls = 0
        self.failures: list[Exception] = []
        self.gate: asyncio.Event | None = None

    async def send(self, email: OutgoingEmail) -> None:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(email)
