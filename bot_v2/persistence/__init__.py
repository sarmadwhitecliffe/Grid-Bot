"""
Persistence Layer

Handles loading and saving of bot state to persistent storage (JSON files).
Provides atomic writes, error handling, and backup capabilities.

Components:
- state_manager: JSON I/O for positions, capitals, and trade history
- backup_manager: Automatic backups and retention management
"""

from bot_v2.persistence.state_manager import StateManager

__all__ = ["StateManager"]
