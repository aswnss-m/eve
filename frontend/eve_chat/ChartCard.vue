<template>
	<div class="eve-chart">
		<div v-if="chart.title" class="eve-chart-title">{{ chart.title }}</div>
		<div ref="el" class="eve-chart-canvas"></div>
		<div v-if="chart.partial_last" class="eve-chart-caption">{{ partialCaption }}</div>

		<table v-if="showData" class="eve-chart-data">
			<thead>
				<tr>
					<th></th>
					<th v-for="d in chart.datasets" :key="d.name">{{ d.name }}</th>
				</tr>
			</thead>
			<tbody>
				<tr v-for="(label, i) in chart.labels" :key="i">
					<td>{{ label }}</td>
					<td v-for="d in chart.datasets" :key="d.name">{{ formatValue(d.values[i]) }}</td>
				</tr>
			</tbody>
		</table>

		<div class="eve-chart-actions">
			<button type="button" class="eve-chart-btn" @click="showData = !showData">
				{{ showData ? __("Hide data") : __("Show data") }}
			</button>
			<button type="button" class="eve-chart-btn" @click="download">{{ __("Download") }}</button>
		</div>
	</div>
</template>

<script setup>
// A chart from the show_chart tool, drawn with the Desk's own frappe.Chart (frappe-charts).
// Every value comes from the tool output, which the server copied from one query's rows.
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

const __ = window.__;
const props = defineProps({ part: { type: Object, required: true } });
const el = ref(null);
const showData = ref(false);
let instance = null;

const chart = computed(() => props.part.output.chart);
const lastLabel = computed(() => chart.value.labels[chart.value.labels.length - 1]);
const partialCaption = computed(() => `* ${lastLabel.value} ${__("to date")}`);

function formatValue(value) {
	if (chart.value.unit === "currency") {
		const currency = frappe.boot && frappe.boot.sysdefaults && frappe.boot.sysdefaults.currency;
		return format_currency(value, currency, 0);
	}
	return format_number(value);
}

function chartOptions() {
	const c = chart.value;
	const labels = c.partial_last ? [...c.labels.slice(0, -1), `${lastLabel.value}*`] : c.labels;
	const options = {
		type: c.type,
		height: 230,
		data: { labels, datasets: c.datasets },
		tooltipOptions: { formatTooltipY: formatValue },
	};
	if (c.type === "line") {
		options.lineOptions = { regionFill: 1, dotSize: 4 };
		options.axisOptions = { xIsSeries: 1, shortenYAxisNumbers: 1 };
	} else if (c.type === "bar") {
		options.axisOptions = { shortenYAxisNumbers: 1, xAxisMode: "tick" };
	} else {
		options.truncateLegends = 1;
		options.maxSlices = c.labels.length;
	}
	return options;
}

function download() {
	if (instance) instance.export();
}

onMounted(() => {
	if (window.frappe && frappe.Chart && el.value) instance = new frappe.Chart(el.value, chartOptions());
});

onBeforeUnmount(() => {
	if (instance && instance.destroy) instance.destroy();
	instance = null;
});
</script>

<style>
.eve-chart {
	margin: 8px 0;
	padding: 8px 4px 6px;
	border: 1px solid var(--border-color, #e2e6e9);
	border-radius: 14px;
	background: var(--fg-color, #fff);
	font-size: var(--text-sm, 12px);
	color: var(--text-color, #1f272e);
}
.eve-chart-title {
	padding: 0 10px;
	font-weight: 600;
}
.eve-chart-canvas {
	overflow: hidden;
}
.eve-chart-caption {
	padding: 0 10px;
	color: var(--text-muted, #6c7680);
	font-size: var(--text-xs, 11px);
}
.eve-chart-data {
	width: calc(100% - 20px);
	margin: 6px 10px;
	border-collapse: collapse;
}
.eve-chart-data th,
.eve-chart-data td {
	padding: 2px 6px;
	border-bottom: 1px solid var(--border-color, #e2e6e9);
	text-align: right;
}
.eve-chart-data th:first-child,
.eve-chart-data td:first-child {
	text-align: left;
}
.eve-chart-actions {
	display: flex;
	gap: 6px;
	padding: 4px 10px 0;
}
.eve-chart-btn {
	border: 1px solid var(--border-color, #e2e6e9);
	border-radius: 999px;
	background: transparent;
	padding: 1px 9px;
	font-size: var(--text-xs, 11px);
	color: var(--text-muted, #6c7680);
	cursor: pointer;
}
.eve-chart-btn:hover {
	color: var(--text-color, #1f272e);
}
</style>
