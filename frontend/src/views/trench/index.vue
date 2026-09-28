<template>
  <section class="page" data-module="trench">
    <header class="page-head">
      <div>
        <h2>管沟巡检管理</h2>
        <p class="page-desc">维护管沟段，围绕管沟编号、管沟位置、沟内管线、积水情况做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记管沟段</button>
        <button class="btn" type="button" @click="exportRows">导出管沟巡检清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.cls">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>风险等级</span>
        <select v-model="riskFilter">
          <option value="">全部</option>
          <option v-for="level in riskLevels" :key="level" :value="level">{{ level }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <span
              v-if="column === '风险等级'"
              class="risk-tag"
              :class="riskClass(String(row[column] ?? ''))"
              :title="String(row['风险说明'] ?? '判定依据缺失')"
            >
              {{ row[column] ?? '—' }}
            </span>
            <span v-else :title="column === '风险说明' ? String(row[column] ?? '') : undefined">
              {{ row[column] ?? '—' }}
            </span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无管沟巡检数据，可先登记管沟段</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条管沟巡检记录（风险等级与判定依据以后端统一判定为准，悬停等级查看理由）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/trench'
const columns = ["管沟编号", "管沟位置", "沟内管线", "积水情况", "盖板完好", "气体浓度", "巡检日期", "风险等级", "风险说明", "管沟状态"]
const actions = ["疏排积水", "更换盖板", "强制通风"]
const statuses = ["正常", "积水", "盖板破损", "气体积聚"]
const riskLevels = ["高风险", "中风险", "低风险"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const riskFilter = ref('')
const filterFields = ["管沟编号", "管沟位置", "沟内管线"]
// 统计卡片口径来自后端 /risk-summary，与列表同一份判定，前端不自行估算。
const stats = ref([
  { label: '高风险管沟', value: 0, cls: 'risk-high' },
  { label: '中风险管沟', value: 0, cls: 'risk-medium' },
  { label: '低风险管沟', value: 0, cls: 'risk-low' },
])

function riskClass(level: string): string {
  if (level === '高风险') return 'risk-high'
  if (level === '中风险') return 'risk-medium'
  if (level === '低风险') return 'risk-low'
  return ''
}

function resetFilters() {
  filters.value = {}
  riskFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '管沟段登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('管沟巡检动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadSummary()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '管沟巡检操作失败'
  }
}

async function loadSummary() {
  try {
    const response = await request(`${ENDPOINT}/risk-summary`)
    if (!response.ok) return
    const summary = await response.json() as Record<string, number>
    stats.value[0].value = summary['高风险'] ?? 0
    stats.value[1].value = summary['中风险'] ?? 0
    stats.value[2].value = summary['低风险'] ?? 0
  } catch {
    // 统计加载失败不阻塞列表，页脚已有错误提示位
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  const risk = riskFilter.value ? `&risk_level=${encodeURIComponent(riskFilter.value)}` : ''
  try {
    const response = await request(`${ENDPOINT}?${query}${risk}`)
    if (!response.ok) {
      throw new Error('管沟段列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadSummary()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '管沟巡检列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.risk-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
}

.risk-high {
  color: #c62828;
  background: #fdecea;
}

.risk-medium {
  color: #ef6c00;
  background: #fff4e5;
}

.risk-low {
  color: #2e7d32;
  background: #e8f5e9;
}
</style>
