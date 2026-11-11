"""Создаёт ключ подписи для приложения Android — один раз, на всё время жизни приложения.

Зачем: Android ставит новую версию поверх старой (с сохранением входа и настроек) только если обе подписаны ОДНИМ ключом.
Без постоянного ключа каждая сборка в GitHub Actions подписывается новым временным, и обновить приложение можно только
удалив старое — вместе с его данными.

Как пользоваться (нужен Python и библиотека: pip install cryptography):
    python android-app/make_keystore.py
Скрипт создаст файл moodle-offline.p12 рядом с собой и напечатает три значения. Положите их в GitHub:
    репозиторий → Settings → Secrets and variables → Actions → New repository secret
        ANDROID_KEYSTORE_BASE64    — длинная строка из вывода
        ANDROID_KEYSTORE_PASSWORD  — пароль из вывода
        ANDROID_KEY_ALIAS          — moodle-offline
Необязательно: --dir ПАПКА (куда положить .p12) и --secrets-file ФАЙЛ (записать три значения в файл, а не на экран —
так делает проверка в GitHub Actions, где вывод попал бы в журнал).
Файл .p12 и пароль храните в надёжном месте (например, в менеджере паролей) и НЕ коммитьте: кто их получит, сможет выпускать
«обновления» от вашего имени. Потеряете — ничего страшного, но следующую версию придётся ставить, удалив старую.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import secrets
import sys
from pathlib import Path

ALIAS = "moodle-offline"


def main() -> int:
    ap = argparse.ArgumentParser(description="Создаёт ключ подписи приложения Android")
    ap.add_argument("--dir", help="папка для moodle-offline.p12 (по умолчанию — рядом со скриптом)")
    ap.add_argument("--secrets-file", help="записать три значения в этот файл вместо вывода на экран")
    args = ap.parse_args()
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import pkcs12
        from cryptography.x509.oid import NameOID
    except ImportError:
        print("Нужна библиотека cryptography:  pip install cryptography")
        return 1
    out = (Path(args.dir).resolve() if args.dir else Path(__file__).resolve().parent) / "moodle-offline.p12"
    if out.exists():
        print(f"Файл {out} уже есть — не перезаписываю (иначе вы потеряете прежний ключ). Уберите его вручную, если нужен новый.")
        return 1
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Moodle Offline"), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Moodle Offline")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365 * 30)).sign(key, hashes.SHA256()))
    password = secrets.token_urlsafe(18)
    data = pkcs12.serialize_key_and_certificates(ALIAS.encode(), key, cert, None, serialization.BestAvailableEncryption(password.encode()))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    b64 = base64.b64encode(data).decode()
    if args.secrets_file:
        sf = Path(args.secrets_file)
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(f"ANDROID_KEY_ALIAS={ALIAS}\nANDROID_KEYSTORE_PASSWORD={password}\nANDROID_KEYSTORE_BASE64={b64}\n", encoding="utf-8")
        print(f"Создан {out}; значения записаны в {sf}")
        return 0
    print(f"Создан {out}\n")
    print("Три секрета для GitHub (Settings → Secrets and variables → Actions):\n")
    print(f"ANDROID_KEY_ALIAS = {ALIAS}")
    print(f"ANDROID_KEYSTORE_PASSWORD = {password}")
    print("ANDROID_KEYSTORE_BASE64 =")
    print(b64)
    print("\nСохраните файл и пароль в надёжном месте. Не коммитьте их в репозиторий.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
