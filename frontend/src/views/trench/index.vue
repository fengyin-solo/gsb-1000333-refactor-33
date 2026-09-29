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
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>风险等级</span>
        <select v-model="riskStatus">
          <option value="">全部等级</option>
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
          <td v-for="column in columns" :key="column" :title="column === '风险等级' ? riskTitle(row) : undefined">
            {{ row[column] ?? '—' }}
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
      <span>共 {{ total }} 条管沟巡检记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | string[] | null>

const ENDPOINT = '/api/trench'
const columns = ["管沟编号", "管沟位置", "沟内管线", "积水情况", "盖板完好", "气体浓度", "巡检日期", "风险等级", "风险分值", "风险说明"]
const actions = ["疏排积水", "更换盖板", "强制通风"]
const riskLevels = ["数据缺失", "低风险", "中风险", "高风险"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const riskStatus = ref('')
const filterFields = ["管沟编号"]

const stats = computed(() => riskLevels.map((level) => ({
  label: level,
  value: rows.value.filter((row) => String(row['风险等级'] ?? '') === level).length,
})))

function riskTitle(row: Row): string {
  const reason = row['风险说明']
  return typeof reason === 'string' ? reason : ''
}

function resetFilters() {
  filters.value = {}
  riskStatus.value = ''
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
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '管沟巡检操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  Object.entries(filters.value).forEach(([field, value]) => {
    if (value) params.set(field === '管沟编号' ? 'keyword' : field, value)
  })
  if (riskStatus.value) {
    params.set('status', riskStatus.value)
  }
  params.set('size', '200')
  const query = params.toString()

  try {
    const response = await request(query ? `${ENDPOINT}?${query}` : ENDPOINT)
    if (!response.ok) {
      throw new Error('管沟段列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '管沟巡检列表读取失败'
  }
}

onMounted(reload)
</script>
