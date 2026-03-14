import type { MacroData } from '../types';

interface MacroDashContextProps {
  ticker: string;
  data: MacroData;
}

function RSIGauge({ rsi }: { rsi: number }) {
  const color =
    rsi < 30 ? 'text-green-500' : rsi > 70 ? 'text-red-500' : 'text-yellow-500';
  const label =
    rsi < 30 ? 'Oversold' : rsi > 70 ? 'Overbought' : 'Neutral';

  return (
    <div className="flex flex-col items-center">
      <span className={`text-xl font-mono font-semibold ${color}`}>
        {rsi.toFixed(1)}
      </span>
      <span className="text-xs text-gray-500">RSI</span>
      <span className={`text-xs ${color}`}>{label}</span>
    </div>
  );
}

function MACDDisplay({ macd, signal }: { macd: number; signal: number }) {
  const bullish = macd > signal;
  return (
    <div className="flex flex-col items-center">
      <span className={`text-xl font-mono font-semibold ${bullish ? 'text-green-500' : 'text-red-500'}`}>
        {macd > 0 ? '+' : ''}{macd.toFixed(2)}
      </span>
      <span className="text-xs text-gray-500">MACD</span>
      <span className={`text-xs ${bullish ? 'text-green-600' : 'text-red-600'}`}>
        {bullish ? '▲ Bullish' : '▼ Bearish'}
      </span>
    </div>
  );
}

function MacroIndicator({ label, value, unit }: { label: string; value: number; unit?: string }) {
  return (
    <div className="flex flex-col items-center">
      <span className="text-xl font-mono font-semibold text-gray-200">
        {value.toFixed(1)}{unit ?? ''}
      </span>
      <span className="text-xs text-gray-500">{label}</span>
    </div>
  );
}

export function MacroDashContext({ ticker, data }: MacroDashContextProps) {
  const hasData = Object.keys(data).length > 0;

  return (
    <div className="bg-gray-900 border-t border-gray-800 px-6 py-3">
      <div className="flex items-center gap-8">
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-gray-600 uppercase tracking-widest">Macro</span>
          <span className="text-sm font-mono font-semibold text-blue-400">{ticker}</span>
        </div>

        {!hasData ? (
          <span className="text-xs text-gray-600">Loading market data…</span>
        ) : (
          <div className="flex items-center gap-8 overflow-x-auto">
            {data.price !== undefined && (
              <div className="flex flex-col items-center">
                <span className="text-xl font-mono font-semibold text-gray-100">
                  ${data.price.toFixed(2)}
                </span>
                <span className="text-xs text-gray-500">Price</span>
                {data.change_pct !== undefined && (
                  <span className={`text-xs font-mono ${data.change_pct >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                    {data.change_pct >= 0 ? '+' : ''}{data.change_pct.toFixed(2)}%
                  </span>
                )}
              </div>
            )}

            {data.rsi !== undefined && <RSIGauge rsi={data.rsi} />}

            {data.macd !== undefined && data.macd_signal !== undefined && (
              <MACDDisplay macd={data.macd} signal={data.macd_signal} />
            )}

            {data.gdp_growth !== undefined && (
              <MacroIndicator label="GDP Growth" value={data.gdp_growth} unit="%" />
            )}

            {data.pce !== undefined && (
              <MacroIndicator label="PCE" value={data.pce} unit="%" />
            )}
          </div>
        )}

        <div className="ml-auto shrink-0">
          <span className="text-xs text-gray-700">Live · Nova Act</span>
        </div>
      </div>
    </div>
  );
}
