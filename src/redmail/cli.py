"""Выгрузка переписки и перенос профиля из командной строки — для
администратора (режим «только администратор» в оформлении организации) и
для сценариев без окна.

    redmail --export-mail КАТАЛОГ [--format mbox|eml] [--home /home/ivanov]
    redmail --export-profile ФАЙЛ.rmprofile [--home /home/ivanov]
    redmail --import-profile ФАЙЛ.rmprofile [--home /home/ivanov]

--home — домашний каталог сотрудника, чьи данные выгружаются (администратор
запускает от root). Программа сотрудника при загрузке профиля должна быть
закрыта.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

COMMANDS = ("--export-mail", "--export-profile", "--import-profile")


def wants_cli(argv: list[str]) -> bool:
    return any(arg.split("=", 1)[0] in COMMANDS for arg in argv[1:])


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="redmail", description="Выгрузка переписки и перенос профиля")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export-mail", metavar="КАТАЛОГ", help="выгрузить всю переписку в новый каталог")
    group.add_argument("--export-profile", metavar="ФАЙЛ", help="выгрузить профиль в файл .rmprofile")
    group.add_argument("--import-profile", metavar="ФАЙЛ", help="загрузить профиль из файла .rmprofile")
    parser.add_argument("--format", choices=("mbox", "eml"), default="mbox", help="формат выгрузки переписки")
    parser.add_argument("--home", metavar="КАТАЛОГ", help="домашний каталог сотрудника (для администратора)")
    args = parser.parse_args(argv[1:])

    if args.home:
        home = Path(args.home)
        if not home.is_dir():
            parser.error(f"нет каталога {home}")
        os.environ["HOME"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(home / ".config")

    from redmail import applog, mail_export, profile_transfer

    if not (args.home and profile_transfer.is_admin()):
        # От root с --home журнал сотрудника не трогаем: при ротации файл
        # достался бы root и программа сотрудника перестала бы в него писать.
        # Запись о действии администратора всё равно уходит в системный журнал.
        applog.setup_logging()
    mode, brand = profile_transfer.export_policy()
    if not profile_transfer.allowed_here():
        print(
            f"Выгрузку переписки и перенос профиля выполняет администратор (оформление «{brand.name}»). "
            "Запустите команду от root с --home."
        )
        profile_transfer.audit("Отказ в выгрузке", команда=" ".join(argv[1:]))
        return 2
    try:
        if args.export_mail:
            def progress(count: int) -> None:
                print(f"\rВыгружено писем: {count}", end="", flush=True)

            result = mail_export.export_mail(Path(args.export_mail), args.format, progress=progress)
            print()
            profile_transfer.audit(
                "Выгрузка переписки", формат=args.format, каталог=result.target, писем=result.messages,
                без_тела=result.headers_only, не_выгружено=result.failed, архивов=result.archives, режим=mode,
            )
            print(f"Готово: {result.messages} писем, архивов {result.archives}, в {result.target}")
            if result.headers_only:
                print(f"Без тела (не было скачано с сервера): {result.headers_only}")
            if result.failed:
                print(f"Не выгружено из-за ошибок разбора: {result.failed} (подробности в журнале программы)")
        elif args.export_profile:
            manifest = profile_transfer.export_profile(Path(args.export_profile), progress=lambda name: print(name))
            print(f"Готово: файлов {len(manifest['files'])}. Пароли не переносятся — на новом компьютере их вводят заново.")
        else:
            backup = profile_transfer.import_profile(Path(args.import_profile), progress=lambda name: print(name))
            print(f"Профиль загружен. Прежние данные сохранены в {backup}")
        return 0
    except (profile_transfer.TransferError, FileExistsError, ValueError, OSError) as exc:
        print(f"Ошибка: {exc}")
        return 1
