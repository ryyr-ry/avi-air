"""
FlyTeam Collector - データモデル定義

各Entityの責務:
  - Aircraft: 機体マスタ（不変に近い固有情報）
  - AircraftHistory: 運用履歴（所属・ステータスの時系列遷移）
  - AircraftAlias: 別機体記号の紐付け
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Aircraft:
    """機体マスタ情報"""
    registration_number: str
    serial_number: Optional[str] = None
    hex_code: Optional[str] = None

    def __post_init__(self):
        if not self.registration_number or not self.registration_number.strip():
            raise ValueError("registration_number must not be empty")
        self.registration_number = self.registration_number.strip().upper()
        if self.serial_number is not None:
            self.serial_number = self.serial_number.strip() or None
        if self.hex_code is not None:
            self.hex_code = self.hex_code.strip().upper() or None


@dataclass
class AircraftHistory:
    """機体の運用履歴（1レジ番 × 1航空会社 × 1期間 = 1レコード）"""
    registration_number: str
    airline_slug: str        # URLスラッグ (例: "starflyer", "skymark")
    airline_name: str        # 表示名 (例: "スターフライヤー")
    model: str               # 機種 (例: "A320neo")
    operating_status: str    # 状況 (例: "運用中", "抹消")
    term_start: str          # 運用開始 (例: "2025/12")
    term_end: Optional[str] = None  # 運用終了 (継続中はNone)

    def __post_init__(self):
        if not self.registration_number or not self.registration_number.strip():
            raise ValueError("registration_number must not be empty")
        self.registration_number = self.registration_number.strip().upper()
        if not self.term_start or not self.term_start.strip():
            raise ValueError(
                f"term_start must not be empty for {self.registration_number}"
            )
        self.term_start = self.term_start.strip()
        self.airline_slug = (self.airline_slug or "").strip()
        self.airline_name = (self.airline_name or "").strip()
        self.model = (self.model or "").strip()
        self.operating_status = (self.operating_status or "").strip()
        if self.term_end is not None:
            self.term_end = self.term_end.strip() or None


@dataclass
class AircraftAlias:
    """同一実機体が別レジ番で登録された履歴の紐付け"""
    base_registration: str
    alias_registration: str

    def __post_init__(self):
        if not self.base_registration or not self.base_registration.strip():
            raise ValueError("base_registration must not be empty")
        if not self.alias_registration or not self.alias_registration.strip():
            raise ValueError("alias_registration must not be empty")
        self.base_registration = self.base_registration.strip().upper()
        self.alias_registration = self.alias_registration.strip().upper()
        if self.base_registration == self.alias_registration:
            raise ValueError(
                f"base and alias must differ: {self.base_registration}"
            )
