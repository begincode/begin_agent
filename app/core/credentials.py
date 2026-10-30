import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.model_gateway.errors import ProviderNotConfiguredError


class CredentialCipher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _fernet(self, *, create: bool) -> Fernet:
        key = self.settings.credential_encryption_key
        if key is not None:
            value = key.get_secret_value().encode()
        else:
            path = Path(self.settings.credential_key_file)
            if create and self.settings.environment == "local":
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # Publish a fully written key atomically. Concurrent workers never see
                # an empty file and never overwrite another worker's master key.
                if not path.exists():
                    fd, temporary = tempfile.mkstemp(dir=path.parent)
                    try:
                        with os.fdopen(fd, "wb") as output:
                            output.write(Fernet.generate_key())
                            output.flush()
                            os.fsync(output.fileno())
                        try:
                            os.link(temporary, path)
                        except FileExistsError:
                            pass
                    finally:
                        os.unlink(temporary)
            try:
                value = path.read_bytes()
            except OSError:
                raise ProviderNotConfiguredError("模型凭据加密主密钥尚未配置") from None
        try:
            return Fernet(value)
        except (ValueError, TypeError):
            raise ProviderNotConfiguredError("模型凭据加密主密钥格式错误") from None

    def encrypt(self, value: str) -> str:
        return self._fernet(create=True).encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet(create=False).decrypt(value.encode()).decode()
        except InvalidToken:
            raise ProviderNotConfiguredError("模型凭据无法解密，请检查主密钥") from None
