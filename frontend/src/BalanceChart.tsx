import { useState } from "react";
import type { BalanceHistory, Forecast, ForecastVsActual } from "./types";
import { money, wholeMoney } from "./format";
import { layoutPointLabels, monthStarts, niceTicks } from "./chartLayout";
import { TRANSFER_SHORT_TEXT, monthsOfYear, vsMonthsOfYear } from "./forecast";
import { useElementSize } from "./hooks";

const MIN_WIDTH = 620;
const INSET = { left: 92, right: 32, top: 28, bottom: 52 };
/** Approximate width of one 12px character, for label collision checks. */
const CHAR_WIDTH = 6.6;

export function BalanceChart({
  history,
  forecast = null,
  vsActual = null,
  year = "",
}: {
  history: BalanceHistory | null;
  /** The entity's forecast: the months of `year` after the last actual point continue the line, dashed. */
  forecast?: Forecast | null;
  /** The forecast compared with what happened: each month with a snapshot gets a marker at its predicted month-end balance. */
  vsActual?: ForecastVsActual | null;
  year?: string;
}) {
  const [frame, setFrame] = useState<HTMLDivElement | null>(null);
  const size = useElementSize(frame);
  const points = (history?.points || []).map((point) => {
    const isoDate = point.date.length === 7 ? `${point.date}-01` : point.date;
    return { ...point, dateValue: new Date(`${isoDate}T00:00:00Z`).getTime(), balanceValue: Number(point.balance) };
  });
  const lastActual = points.length ? points[points.length - 1].dateValue : -Infinity;
  // Each forecast month is dated at its end; the current month's end lies after the last actual point.
  const projection = monthsOfYear(forecast, year).flatMap((month) => {
    if (!month) return [];
    const [monthYear, monthNumber] = month.month.split("-").map(Number);
    const dateValue = Date.UTC(monthYear, monthNumber, 0);
    return dateValue > lastActual ? [{
      month, dateValue, closing: Number(month.closing), low: Number(month.low), high: Number(month.high),
      short: month.flags.includes("transfer_short"),
    }] : [];
  });
  // The balance each month was predicted to end with, when the forecast was made; dated at the month's end.
  const predictions = vsMonthsOfYear(vsActual, year).flatMap((entry) => {
    if (!entry) return [];
    const [monthYear, monthNumber] = entry.month.split("-").map(Number);
    return [{
      entry, dateValue: Date.UTC(monthYear, monthNumber, 0),
      forecast: Number(entry.balance.forecast), low: Number(entry.balance.low), high: Number(entry.balance.high),
      actual: Number(entry.balance.actual),
    }];
  });
  if (!points.length && !projection.length) {
    return <p className="muted empty-state">No balance history available for this year.</p>;
  }

  // Draw at the container's real pixel size so text and strokes are never scaled.
  const width = Math.max(MIN_WIDTH, size.width);
  const height = size.height;
  const plotWidth = width - INSET.left - INSET.right;
  const plotHeight = height - INSET.top - INSET.bottom;
  // A year without actual points starts on its first forecast month.
  const firstForecast = new Date(projection[0]?.dateValue ?? 0);
  const minDate = points.length ? points[0].dateValue : Date.UTC(firstForecast.getUTCFullYear(), firstForecast.getUTCMonth(), 1);
  const maxDate = Math.max(projection.length ? projection[projection.length - 1].dateValue : lastActual, ...predictions.map((point) => point.dateValue));
  const values = [
    ...points.map((point) => point.balanceValue),
    ...projection.flatMap((point) => [point.closing, point.low, point.high]),
    ...predictions.flatMap((point) => [point.forecast, point.low, point.high]),
  ];
  const ticks = niceTicks(Math.min(...values), Math.max(...values), Math.max(3, Math.round(plotHeight / 70)));
  const minBalance = ticks[0];
  const maxBalance = ticks[ticks.length - 1];
  const x = (dateValue: number) =>
    INSET.left + (maxDate === minDate ? plotWidth / 2 : ((dateValue - minDate) / (maxDate - minDate)) * plotWidth);
  const y = (balance: number) => INSET.top + ((maxBalance - balance) / (maxBalance - minBalance)) * plotHeight;
  const baseline = y(0);
  const labels = layoutPointLabels(
    points.map((point) => ({ x: x(point.dateValue), y: y(point.balanceValue), text: wholeMoney(point.balanceValue) })),
    { charWidth: CHAR_WIDTH, top: 0, bottom: height - INSET.bottom + 22, avoidY: baseline },
  );
  const line = points.map((point) => `${x(point.dateValue)},${y(point.balanceValue)}`).join(" L ");
  const area = `M ${line} L ${x(lastActual)},${baseline} L ${x(minDate)},${baseline} Z`;
  // The dashed line starts at the last actual point; the band covers the forecast months only.
  const forecastLine = [
    ...points.slice(-1).map((point) => `${x(point.dateValue)},${y(point.balanceValue)}`),
    ...projection.map((point) => `${x(point.dateValue)},${y(point.closing)}`),
  ].join(" L ");
  const band = [
    ...projection.map((point) => `${x(point.dateValue)},${y(point.high)}`),
    ...[...projection].reverse().map((point) => `${x(point.dateValue)},${y(point.low)}`),
  ].join(" L ");

  return (
    <>
      <div className="balance-chart-scroll" ref={setFrame}>
        {height > 0 && (
          <svg
            className="balance-chart"
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label="Account balance history for selected year"
          >
            {ticks.map((balance) => (
              <g key={balance}>
                <line
                  className={balance === 0 ? "chart-grid-line chart-zero-line" : "chart-grid-line"}
                  x1={INSET.left}
                  x2={width - INSET.right}
                  y1={y(balance)}
                  y2={y(balance)}
                />
                <text className="chart-axis-label" x={INSET.left - 12} y={y(balance) + 4} textAnchor="end">
                  {wholeMoney(balance)}
                </text>
              </g>
            ))}
            {monthStarts(minDate, maxDate).map((dateValue) => (
              <text className="chart-axis-label" key={dateValue} x={x(dateValue)} y={height - 14} textAnchor="middle">
                {new Date(dateValue).toLocaleDateString(undefined, { month: "short", timeZone: "UTC" })}
              </text>
            ))}
            {projection.length > 0 && <path className="forecast-band" d={`M ${band} Z`} />}
            {points.length > 0 && <path className="balance-area" d={area} />}
            {points.length > 0 && <path className="balance-line" d={`M ${line}`} />}
            {projection.length > 0 && <path className="forecast-line" d={`M ${forecastLine}`} />}
            {points.map((point, index) => {
              const label = labels[index];
              return (
                <g key={point.date}>
                  <circle className="balance-point" cx={label.x} cy={label.y} r="4">
                    <title>{`${point.date}: ${money(point.balanceValue)}`}</title>
                  </circle>
                  {!label.hidden && (
                    <text className="chart-point-label" x={label.x} y={label.labelY} textAnchor={label.anchor}>
                      {label.text}
                    </text>
                  )}
                </g>
              );
            })}
            {projection.map((point) => {
              const range = `${money(point.low)} – ${money(point.high)}`;
              const label = new Date(point.dateValue).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
              return (
                <g key={point.month.month}>
                  <circle className="forecast-point" cx={x(point.dateValue)} cy={y(point.closing)} r="3.5">
                    <title>{`${label}, projected: ${money(point.closing)} (${range})`}</title>
                  </circle>
                  {point.short && (
                    <path
                      className="forecast-shortfall"
                      d={`M ${x(point.dateValue)},${y(point.closing) - 18} l 6,10 l -12,0 Z`}
                    >
                      <title>{`${label}: ${TRANSFER_SHORT_TEXT}`}</title>
                    </path>
                  )}
                </g>
              );
            })}
            {predictions.map((point) => {
              const label = new Date(point.dateValue).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
              const cx = x(point.dateValue);
              const cy = y(point.forecast);
              return (
                <path key={point.entry.month} className="vs-marker" d={`M ${cx},${cy - 6} l 6,6 l -6,6 l -6,-6 Z`}>
                  <title>{`${label}: actual ${money(point.actual)}, predicted ${money(point.forecast)} (${money(point.low)} – ${money(point.high)})`}</title>
                </path>
              );
            })}
          </svg>
        )}
      </div>
      {predictions.length > 0 && (
        <p className="chart-note muted small">Diamonds: the month-end balance the forecast predicted when the forecast was made, to compare with the line.</p>
      )}
      {projection.length > 0 && (
        <p className="chart-note muted small">
          Dashed: forecast, with its range from the backtest.
          {forecast && forecast.accounts.length > 0 && ` Balances as of ${forecast.accounts.map((account) => `${account.name} ${account.as_of}`).join(", ")}.`}
        </p>
      )}
    </>
  );
}
