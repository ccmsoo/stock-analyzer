"use client";
import {
  ComposedChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  ReferenceArea,
  CartesianGrid,
} from "recharts";
import type { DailyBar } from "@/lib/history";
import { formatDate } from "@/lib/format";

interface Props {
  bars: DailyBar[];
  signalDate?: string;
  entryPrice?: number | null;
  stopLoss?: number | null;
  takeProfit?: number | null;
}

export function PriceChart({ bars, signalDate, entryPrice, stopLoss, takeProfit }: Props) {
  const data = bars.map((b) => ({
    date: b.date,
    label: b.date.slice(4),
    close: b.close,
    volume: b.volume,
  }));

  return (
    <div className="w-full">
      <div className="h-60 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: "#71717a" }}
              tickLine={false}
              axisLine={{ stroke: "#27272a" }}
              minTickGap={20}
            />
            <YAxis
              yAxisId="price"
              tick={{ fontSize: 10, fill: "#71717a" }}
              tickLine={false}
              axisLine={false}
              orientation="right"
              tickFormatter={(v) => v.toLocaleString()}
              domain={["dataMin", "dataMax"]}
              width={60}
            />
            <Tooltip
              contentStyle={{
                background: "#18181b",
                border: "1px solid #27272a",
                borderRadius: 6,
                fontSize: 12,
              }}
              labelFormatter={(label, payload) => {
                const item = payload?.[0]?.payload as { date?: string } | undefined;
                return item?.date ? formatDate(item.date) : String(label);
              }}
              formatter={(value) => [Number(value).toLocaleString(), "종가"]}
            />
            {entryPrice && (
              <ReferenceLine
                yAxisId="price"
                y={entryPrice}
                stroke="#a3a3a3"
                strokeDasharray="3 3"
                label={{ value: "진입", fontSize: 10, fill: "#a3a3a3", position: "insideTopLeft" }}
              />
            )}
            {stopLoss && (
              <ReferenceLine
                yAxisId="price"
                y={stopLoss}
                stroke="#60a5fa"
                strokeDasharray="3 3"
                label={{ value: "손절", fontSize: 10, fill: "#60a5fa", position: "insideTopLeft" }}
              />
            )}
            {takeProfit && (
              <ReferenceLine
                yAxisId="price"
                y={takeProfit}
                stroke="#fb7185"
                strokeDasharray="3 3"
                label={{ value: "익절", fontSize: 10, fill: "#fb7185", position: "insideTopLeft" }}
              />
            )}
            {signalDate && (
              <ReferenceArea
                yAxisId="price"
                x1={signalDate.slice(4)}
                x2={signalDate.slice(4)}
                stroke="#fbbf24"
                strokeOpacity={0.4}
                label={{ value: "D", fontSize: 10, fill: "#fbbf24", position: "insideTop" }}
              />
            )}
            <Line
              yAxisId="price"
              type="monotone"
              dataKey="close"
              stroke="#e4e4e7"
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
