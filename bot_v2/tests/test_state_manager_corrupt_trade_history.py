from bot_v2.persistence.state_manager import StateManager


def test_load_trade_history_renames_corrupt_file(tmp_path):
    # Setup a temporary data dir and write a corrupt trade_history.json
    data_dir = tmp_path / "data_futures"
    data_dir.mkdir()
    history_file = data_dir / "trade_history.json"

    # Write invalid JSON
    with open(history_file, "w", encoding="utf-8") as f:
        f.write("{ invalid json")

    sm = StateManager(data_dir=data_dir)

    # Call load_trade_history - expected behavior: corrupt file is renamed to .corrupt.<ts> and new empty list is returned
    history = sm.load_trade_history()
    assert isinstance(history, list)
    assert history == [], "Expected empty history fallback on corrupt file"

    # Look for any file matching trade_history.json.corrupt*
    corrupt_files = list(data_dir.glob("trade_history.json.corrupt*"))
    assert corrupt_files, "Expected corrupt file rename to exist but none found"

    # Original file should either be replaced with a fresh empty file or absent; check original exists and is valid JSON
    assert (
        history_file.exists()
    ), "Expected trade_history.json to exist (possibly recreated)"
    # Read content - must be valid JSON (empty list or similar)
    with open(history_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert (
            content.strip() != "{ invalid json"
        ), "Original corrupt content should not remain"
