class FakeTurnstileVerifier:
    """Accepts the token "pass"; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def verify(self, token: str, remote_ip: str) -> bool:
        self.calls.append((token, remote_ip))
        return token == "pass"
