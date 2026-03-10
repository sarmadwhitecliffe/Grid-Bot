import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from bot_v2.risk.capital_manager import CapitalManager

@pytest.fixture
def temp_data_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)

def test_normal_save_path(temp_data_dir):
    manager = CapitalManager(data_dir=temp_data_dir)
    manager._capitals = {"BTCUSDT": {"capital": "1000.00", "tier": "PROBATION"}}
    manager._save()
    
    assert manager.capitals_file.exists()
    with open(manager.capitals_file, "r") as f:
        data = json.load(f)
    assert "BTCUSDT" in data
    assert data["BTCUSDT"]["capital"] == "1000.00"

def test_os_replace_failure(temp_data_dir, mocker):
    manager = CapitalManager(data_dir=temp_data_dir)
    manager._capitals = {"BTCUSDT": {"capital": "1000.00", "tier": "PROBATION"}}
    
    # Save a valid initial file
    manager._save()
    assert manager.capitals_file.exists()
    initial_content = manager.capitals_file.read_text()
    
    # Mock os.replace to raise an exception
    mocker.patch("os.replace", side_effect=OSError("Disk full"))
    
    # Attempt to save
    manager._capitals["BTCUSDT"]["capital"] = "2000.00"
    manager._save()
    
    # Verify original file is untouched
    assert manager.capitals_file.read_text() == initial_content
    
    # Verify no temp files left behind
    temp_files = list(temp_data_dir.glob("*.tmp"))
    assert len(temp_files) == 0

def test_no_tmp_files_accumulate(temp_data_dir):
    manager = CapitalManager(data_dir=temp_data_dir)
    manager._capitals = {"BTCUSDT": {"capital": "1000.00", "tier": "PROBATION"}}
    
    # Save multiple times
    for _ in range(10):
        manager._save()
        
    temp_files = list(temp_data_dir.glob("*.tmp"))
    assert len(temp_files) == 0

def test_process_kill_during_save_loop(temp_data_dir, mocker):
    manager = CapitalManager(data_dir=temp_data_dir)
    manager._capitals = {"BTCUSDT": {"capital": "1000.00", "tier": "PROBATION"}}
    manager._save()
    
    initial_content = manager.capitals_file.read_text()
    
    # Mock json.dump to raise an error simulating a kill during writing to temp file
    mocker.patch("json.dump", side_effect=SystemExit("Process killed"))
    
    manager._capitals["BTCUSDT"]["capital"] = "2000.00"
    
    # Catch the system exit
    try:
        manager._save()
    except SystemExit:
        pass
        
    # Verify original file is perfectly intact (not corrupted or half-written)
    assert manager.capitals_file.read_text() == initial_content
