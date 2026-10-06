import asyncio

from jev_board import cli, config, tui


def test_failed_load_notifies_instead_of_exiting(monkeypatch):
    def broken(_s):
        raise SystemExit("gh project view failed: not found")
    monkeypatch.setattr(cli, "prepare", broken)
    app = tui.JevBoard(config.resolve({"board": 7, "owner": "o"}))

    async def drive():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            assert app.is_running
            assert any("not found" in str(n.message) for n in app._notifications)
            assert "load failed" in app.sub_title
    asyncio.run(drive())
