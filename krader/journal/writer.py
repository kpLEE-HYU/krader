"""Markdown rendering for daily trading journal."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from krader.journal.types import CandleSnapshot, DailySummary, TradeEntry, TradeJournal


class JournalWriter:
    """Renders TradeJournal to markdown file."""

    def write(self, journal: TradeJournal, output_path: Path) -> Path:
        """Write journal to markdown file, return the path."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        content = self._render(journal)
        output_path.write_text(content, encoding="utf-8")
        return output_path

    def _render(self, journal: TradeJournal) -> str:
        parts: list[str] = []
        date_str = journal.date.strftime("%Y-%m-%d")

        parts.append(f"# 일일 트레이딩 저널 - {date_str}\n")
        parts.append(self._render_summary(journal.summary))

        for idx, trade in enumerate(journal.trades, 1):
            parts.append("---\n")
            parts.append(self._render_trade(idx, trade))

        parts.append("---\n")
        parts.append(self._render_portfolio(journal.portfolio_equity, journal.portfolio_cash))

        return "\n".join(parts)

    def _render_summary(self, summary: DailySummary) -> str:
        symbols_display = ", ".join(summary.symbols_traded[:3])
        if len(summary.symbols_traded) > 3:
            symbols_display += " 외"

        lines = [
            "## 일일 요약",
            "| 항목 | 값 |",
            "|------|-----|",
            f"| 총 거래 | {summary.total_trades}건 (매수 {summary.buy_count}, 매도 {summary.sell_count}) |",
            f"| 총 수수료 | {_format_krw(summary.total_commission)} |",
            f"| 거래 종목 | {symbols_display} |",
            f"| 전략 | {summary.strategy_name} |",
            "",
        ]
        return "\n".join(lines)

    def _render_trade(self, idx: int, trade: TradeEntry) -> str:
        side_kr = "매수" if trade.side == "BUY" else "매도"
        time_str = trade.created_at.strftime("%H:%M:%S")
        confidence_pct = int(trade.confidence * 100)

        lines = [
            f"## 거래 #{idx}: {trade.symbol} {side_kr}",
            f"**시각:** {time_str} | **전략:** {trade.strategy_name} | **신뢰도:** {confidence_pct}%",
            "",
        ]

        # 진입 사유
        lines.append("### 진입 사유")
        lines.append(f"> {trade.reason}")
        lines.append("")

        # 진입 전 캔들 (1분봉)
        if trade.candles_before:
            lines.append("### 시세 (1분봉, 진입 전)")
            lines.append(self._render_candle_table(trade.candles_before, trade.created_at))
            lines.append("")

        # 체결 내역
        lines.append("### 체결 내역")
        order_type_kr = "시장가" if trade.order_type == "MARKET" else "지정가"
        lines.append(f"- 주문유형: {order_type_kr}")
        lines.append(f"- 체결가: {_format_krw(trade.avg_fill_price)}")
        lines.append(f"- 수량: {trade.quantity}주")
        lines.append(f"- 체결금액: {_format_krw(trade.notional_value)}")
        lines.append(f"- 수수료: {_format_krw(trade.total_commission)}")
        lines.append("")

        # 진입 후 추이 (5분봉)
        if trade.candles_after:
            lines.append("### 진입 후 추이 (5분봉)")
            lines.append(self._render_after_table(trade.candles_after, trade.avg_fill_price))
            lines.append("")

        return "\n".join(lines)

    def _render_candle_table(
        self, candles: list[CandleSnapshot], entry_time: datetime
    ) -> str:
        lines = [
            "| 시간 | 시가 | 고가 | 저가 | 종가 | 거래량 |",
            "|------|------|------|------|------|--------|",
        ]

        entry_minute = entry_time.replace(second=0, microsecond=0)

        for c in candles:
            time_str = c.open_time.strftime("%H:%M")
            candle_minute = c.open_time.replace(second=0, microsecond=0)
            is_entry = candle_minute == entry_minute

            if is_entry:
                lines.append(
                    f"| **{time_str}** | **{_format_krw(c.open)}** | "
                    f"**{_format_krw(c.high)}** | **{_format_krw(c.low)}** | "
                    f"**{_format_krw(c.close)}** | **{c.volume:,}** | ← 진입"
                )
            else:
                lines.append(
                    f"| {time_str} | {_format_krw(c.open)} | "
                    f"{_format_krw(c.high)} | {_format_krw(c.low)} | "
                    f"{_format_krw(c.close)} | {c.volume:,} |"
                )

        return "\n".join(lines)

    def _render_after_table(
        self, candles: list[CandleSnapshot], entry_price: Decimal
    ) -> str:
        lines = [
            "| 시간 | 종가 | 등락률 |",
            "|------|------|--------|",
        ]

        for c in candles:
            time_str = c.open_time.strftime("%H:%M")
            if entry_price > 0:
                change_pct = (c.close - entry_price) / entry_price * 100
                sign = "+" if change_pct >= 0 else ""
                lines.append(
                    f"| {time_str} | {_format_krw(c.close)} | {sign}{change_pct:.2f}% |"
                )
            else:
                lines.append(f"| {time_str} | {_format_krw(c.close)} | - |")

        return "\n".join(lines)

    def _render_portfolio(self, equity: Decimal, cash: Decimal) -> str:
        lines = [
            "## 포트폴리오 현황",
            f"- 잔고: {_format_krw(cash)}",
            f"- 총 평가: {_format_krw(equity)}",
            "",
        ]
        return "\n".join(lines)


def _format_krw(value: Decimal) -> str:
    """Format a decimal value as Korean Won."""
    return f"{int(value):,}원"
