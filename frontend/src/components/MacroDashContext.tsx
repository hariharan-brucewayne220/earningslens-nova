import type { MacroData } from '../types';

interface MacroDashContextProps {
  ticker: string;
  data: MacroData;
}

function DataPill({
  label,
  value,
  color,
  sub,
}: {
  label: string;
  value: string;
  color?: string;
  sub?: string;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.06]">
      <div>
        <span
          className="text-sm font-mono font-semibold block leading-tight"
          style={{ color: color ?? '#e2e8f0' }}
        >
          {value}
        </span>
        <span className="section-label block leading-tight">{label}</span>
      </div>
      {sub && (
        <span
          className="text-xs font-medium"
          style={{ color: color ?? 'rgba(148,163,184,0.5)' }}
        >
          {sub}
        </span>
      )}
    </div>
  );
}

export function MacroDashContext({ ticker, data }: MacroDashContextProps) {
  const hasData = Object.keys(data).length > 0;
  const hasNumber = (value: number | undefined | null): value is number =>
    typeof value === 'number' && Number.isFinite(value);
  const hasSignals = [
    data.price,
    data.change_pct,
    data.rsi,
    data.macd,
    data.macd_signal,
    data.gdp_growth,
    data.pce,
  ].some(hasNumber);

  const rsiColor =
    hasNumber(data.rsi)
      ? data.rsi < 30 ? '#10b981'
      : data.rsi > 70 ? '#f43f5e'
      : '#f59e0b'
      : undefined;
  const rsiLabel =
    hasNumber(data.rsi)
      ? data.rsi < 30 ? 'Oversold'
      : data.rsi > 70 ? 'Overbought'
      : 'Neutral'
      : undefined;

  const macdBull = hasNumber(data.macd) && hasNumber(data.macd_signal) && data.macd > data.macd_signal;

  return (
    <div
      className="border-t border-white/[0.06] px-4 py-2 shrink-0"
      style={{ background: 'rgba(6, 13, 31, 0.8)' }}
    >
      <div className="flex items-center gap-3 overflow-x-auto">
        {/* Ticker label */}
        <div className="flex items-center gap-2 shrink-0">
          <span className="section-label">MacroDash</span>
          <span className="text-sm font-mono font-bold text-orange-400">{ticker}</span>
        </div>

        <div className="w-px h-5 bg-white/[0.08] shrink-0" />

        {!hasData ? (
          <span className="text-xs text-slate-600">Fetching market data…</span>
        ) : !hasSignals ? (
          <span className="text-xs text-slate-500">MacroDash connected, but no numeric signals were available for this session yet.</span>
        ) : (
          <div className="flex items-center gap-2 overflow-x-auto">
            {hasNumber(data.price) && (
              <DataPill
                label="Price"
                value={`$${data.price.toFixed(2)}`}
                sub={
                  hasNumber(data.change_pct)
                    ? `${data.change_pct >= 0 ? '+' : ''}${data.change_pct.toFixed(2)}%`
                    : undefined
                }
                color={
                  hasNumber(data.change_pct)
                    ? data.change_pct >= 0 ? '#10b981' : '#f43f5e'
                    : undefined
                }
              />
            )}
            {hasNumber(data.rsi) && (
              <DataPill
                label="RSI"
                value={data.rsi.toFixed(1)}
                color={rsiColor}
                sub={rsiLabel}
              />
            )}
            {hasNumber(data.macd) && hasNumber(data.macd_signal) && (
              <DataPill
                label="MACD"
                value={`${data.macd > 0 ? '+' : ''}${data.macd.toFixed(2)}`}
                color={macdBull ? '#10b981' : '#f43f5e'}
                sub={macdBull ? '▲ Bull' : '▼ Bear'}
              />
            )}
            {hasNumber(data.gdp_growth) && (
              <DataPill label="GDP" value={`${data.gdp_growth.toFixed(1)}%`} />
            )}
            {hasNumber(data.pce) && (
              <DataPill label="PCE" value={`${data.pce.toFixed(1)}%`} />
            )}
          </div>
        )}

        <div className="ml-auto shrink-0 flex items-center gap-1.5">
          <span className="w-1 h-1 rounded-full bg-orange-500/60 pulse-dot" />
          <span className="section-label">Nova Act</span>
        </div>
      </div>
    </div>
  );
}
