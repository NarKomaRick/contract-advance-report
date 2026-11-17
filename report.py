"""Точка входа программы для запуска из командной строки.

Принимает --data-dir, --date и --output, вызывает загрузку CSV, расчёт и сохранение.
Печатает сводку или понятную ошибку и возвращает код завершения.
Правила распределения денег находятся в calculation.py, работа с CSV в csv_io.py.
"""

import argparse
import sys
from pathlib import Path

from calculation import build_report
from csv_io import format_money, load_dataset, parse_date, write_report


def main(argv: list[str] | None = None) -> int:
    """Запускает подготовку отчёта; список argv позволяет отдельно проверять CLI."""

    parser = argparse.ArgumentParser(description="Отчёт об оплате работ и договоров на указанную дату")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Папка исходных CSV (по умолчанию data)")
    parser.add_argument("--date", required=True, help="Дата отчёта включительно, YYYY-MM-DD")
    parser.add_argument("--output", type=Path, help="Новая папка результата (по умолчанию reports/ДАТА)")
    args = parser.parse_args(argv)
    try:
        as_of = parse_date(args.date)
        output = args.output or Path("reports") / as_of.isoformat()
        result = build_report(load_dataset(args.data_dir), as_of)
        write_report(result, output)
    except (ValueError, OSError, UnicodeError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    print(f"Отчёт на {as_of.isoformat()}: {output.resolve()}")
    print("Оплата по работам включает условное распределение общих платежей.")
    for contract in result.contracts:
        print(f"{contract.contract_id}: выплачено {format_money(contract.net_paid)} руб.; "
              f"осталось по работам {format_money(contract.remaining_works)} руб.; "
              f"нераспределённый аванс {format_money(contract.unallocated_advance)} руб.")
        for warning in contract.warnings:
            print(f"  Примечание: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
