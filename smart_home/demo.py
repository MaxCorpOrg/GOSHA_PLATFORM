"""Offline voice demo: python3 -m smart_home.demo 'включи свет на кухне'."""
import asyncio
import json
import sys
from smart_home.core import Device, SmartHome


class Offline:
    def request(self, *args):
        raise RuntimeError('Offline demo cannot contact Home Assistant')


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Передайте одну команду в кавычках. Пример: 'включи свет на кухне'")
    home = SmartHome(Offline(), [Device('light.kitchen', ('свет на кухне',)),
                                Device('cover.bedroom', ('шторы в спальне',)),
                                Device('climate.room', ('климат в комнате',))])
    # Demo intentionally accepts only write plans; no real state is invented.
    try:
        command = home.parse(sys.argv[1])
        if command['action'] == 'read':
            raise ValueError('В демонстрации нет реальных показаний датчиков')
    except ValueError:
        print(json.dumps({'status': 'rejected', 'reason': 'clarification_required_or_no_live_data'}, ensure_ascii=False))
        return
    print(json.dumps(asyncio.run(home.voice(sys.argv[1], 'offline-demo')), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
