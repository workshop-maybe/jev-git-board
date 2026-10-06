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


def test_inbox_mode_labels_itself(monkeypatch):
    def broken(_s):
        raise SystemExit("gh api notifications failed: HTTP 401")
    monkeypatch.setattr(cli, "prepare", broken)
    app = tui.JevBoard(config.resolve({"inbox": True}))

    async def drive():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            assert [c.label.plain for c in app.query_one(tui.DataTable).columns.values()][-1] == "reason"
            assert any("HTTP 401" in str(n.message) for n in app._notifications)
    asyncio.run(drive())
