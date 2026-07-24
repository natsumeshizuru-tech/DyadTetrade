import { BarChart, CandlestickChart, LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, MarkPointComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import type { EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import ReactEChartsCore from 'echarts-for-react/esm/core'

echarts.use([
  BarChart,
  CandlestickChart,
  LineChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  DataZoomComponent,
  MarkPointComponent,
  CanvasRenderer,
])

interface QuantChartProps {
  option: EChartsCoreOption
  height?: number | string
}

export default function QuantChart({ option, height = 360 }: QuantChartProps) {
  return <ReactEChartsCore echarts={echarts} option={option} style={{ height }} />
}
