<script setup>
// M6.5 配置管理页面：全局服务切换 + 三服务卡片
// 功能：启用开关 / Base URL / API Key 脱敏 / 模型列表 / 测试连接 / 保存
import { onMounted, reactive, ref } from 'vue'
import { fetchModels, fetchServices, testService, updateServices } from '../api/services'

const SERVICES = ['deepseek', 'claude', 'openai']

const loading = ref(true)
const loadError = ref('')
const saveMsg = reactive({ text: '', ok: true })

// 全局设置
const globalForm = reactive({ default_service: 'deepseek', compile_service: 'deepseek' })
const globalDirty = ref(false)

// 服务卡片：name → 可编辑表单 + 交互状态
const cards = reactive({})
for (const name of SERVICES) {
  cards[name] = {
    form: { enabled: false, base_url: '', model: '', apiKeyInput: '', showKey: false },
    maskedKey: '',
    models: [],
    modelDynamic: false,
    modelsLoading: false,
    modelsHint: '',
    testState: null, // {ok, message}
    testing: false,
    saving: false,
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await fetchServices()
    globalForm.default_service = data.default_service
    globalForm.compile_service = data.compile_service
    globalDirty.value = false
    for (const name of SERVICES) {
      const s = data.services?.[name]
      if (!s) continue
      const c = cards[name]
      c.form.enabled = !!s.enabled
      c.form.base_url = s.base_url || ''
      c.form.model = s.model || ''
      c.form.apiKeyInput = ''
      c.form.showKey = false
      c.maskedKey = s.api_key || ''
      c.testState = null
    }
  } catch (e) {
    loadError.value = String(e.message || e)
  } finally {
    loading.value = false
  }
}

function flashSave(text, ok = true) {
  saveMsg.text = text
  saveMsg.ok = ok
  setTimeout(() => { saveMsg.text = '' }, 4000)
}

async function saveGlobal() {
  // 「跟随默认」：编译服务固化为当前默认服务（V1 简化，V2 热更新再做动态跟随）
  const compile = globalForm.compile_service === '__default__'
    ? globalForm.default_service : globalForm.compile_service
  try {
    await updateServices({ default_service: globalForm.default_service, compile_service: compile })
    globalForm.compile_service = compile
    globalDirty.value = false
    flashSave('全局配置已保存。配置更新需重启后端服务生效')
  } catch (e) {
    flashSave(`保存失败：${e.message || e}`, false)
  }
}

async function saveService(name) {
  const c = cards[name]
  c.saving = true
  try {
    await updateServices({ services: { [name]: {
      name,
      enabled: c.form.enabled,
      base_url: c.form.base_url,
      // API Key 留空 = 不修改（后端还会挡脱敏值回写，双保险）
      api_key: c.form.apiKeyInput.trim(),
      model: c.form.model,
    } } })
    c.form.apiKeyInput = ''
    await load()
    flashSave(`${name} 配置已保存。配置更新需重启后端服务生效`)
  } catch (e) {
    flashSave(`保存失败：${e.message || e}`, false)
  } finally {
    c.saving = false
  }
}

async function refreshModels(name) {
  const c = cards[name]
  c.modelsLoading = true
  c.modelsHint = ''
  try {
    const data = await fetchModels(name)
    c.models = data.models || []
    c.modelDynamic = !!data.dynamic
    if (data.dynamic) {
      c.modelsHint = `获取到 ${c.models.length} 个模型`
    } else {
      c.modelsHint = `⚠️ 获取失败，请手动输入（${data.error || '服务不支持'}）`
    }
  } catch (e) {
    c.models = []
    c.modelDynamic = false
    c.modelsHint = `⚠️ 获取失败，请手动输入（${e.message || e}）`
  } finally {
    c.modelsLoading = false
  }
}

async function testConn(name) {
  const c = cards[name]
  c.testing = true
  c.testState = null
  try {
    const r = await testService(name)
    c.testState = { ok: !!r.success, message: r.message || '' }
  } catch (e) {
    c.testState = { ok: false, message: String(e.message || e) }
  } finally {
    c.testing = false
  }
}

onMounted(load)
</script>

<template>
  <section class="settings">
    <div class="page-head">
      <h1>LLM 服务配置</h1>
      <p v-if="saveMsg.text" :class="['save-msg', { ok: saveMsg.ok, bad: !saveMsg.ok }]">
        {{ saveMsg.text }}
      </p>
    </div>

    <div v-if="loading" class="tip">加载中…</div>
    <div v-else-if="loadError" class="tip bad">加载失败：{{ loadError }}</div>

    <template v-else>
      <!-- 全局设置 -->
      <div class="card">
        <h2>全局设置</h2>
        <div class="grid">
          <label>
            <span>默认 LLM 服务</span>
            <select v-model="globalForm.default_service" @change="globalDirty = true">
              <option v-for="s in SERVICES" :key="s" :value="s">{{ s }}</option>
            </select>
          </label>
          <label>
            <span>编译专用服务</span>
            <select v-model="globalForm.compile_service" @change="globalDirty = true">
              <option value="__default__">跟随默认</option>
              <option v-for="s in SERVICES" :key="s" :value="s">{{ s }}</option>
            </select>
          </label>
        </div>
        <div class="actions">
          <button :disabled="!globalDirty" @click="saveGlobal">保存全局设置</button>
          <span v-if="globalDirty" class="dirty">有未保存的修改</span>
        </div>
      </div>

      <!-- 服务卡片 -->
      <div v-for="name in SERVICES" :key="name" class="card">
        <div class="card-head">
          <h2>{{ name }}</h2>
          <label class="switch">
            <input v-model="cards[name].form.enabled" type="checkbox" />
            <span>{{ cards[name].form.enabled ? '已启用' : '已禁用' }}</span>
          </label>
        </div>

        <label class="field">
          <span>Base URL</span>
          <input v-model="cards[name].form.base_url" type="text"
                 placeholder="https://api.example.com/v1" />
          <small>大部分服务以 /v1 结尾</small>
        </label>

        <label class="field">
          <span>API Key</span>
          <div class="key-row">
            <input v-model="cards[name].form.apiKeyInput"
                   :type="cards[name].form.showKey ? 'text' : 'password'"
                   :placeholder="cards[name].maskedKey
                     ? `已配置（${cards[name].maskedKey}），留空不修改` : '未配置，请输入密钥'" />
            <button class="ghost" @click="cards[name].form.showKey = !cards[name].form.showKey">
              {{ cards[name].form.showKey ? '隐藏' : '显示' }}
            </button>
          </div>
        </label>

        <label class="field">
          <span>模型</span>
          <div class="key-row">
            <select v-if="cards[name].modelDynamic && cards[name].models.length"
                    v-model="cards[name].form.model">
              <option v-for="m in cards[name].models" :key="m" :value="m">{{ m }}</option>
            </select>
            <input v-else v-model="cards[name].form.model" type="text"
                   placeholder="如 deepseek-chat / gpt-4" />
            <button class="ghost" :disabled="cards[name].modelsLoading"
                    @click="refreshModels(name)">
              {{ cards[name].modelsLoading ? '获取中…' : '刷新模型列表' }}
            </button>
          </div>
          <small v-if="cards[name].modelsHint">{{ cards[name].modelsHint }}</small>
        </label>

        <div class="actions">
          <button :disabled="cards[name].testing" @click="testConn(name)">
            {{ cards[name].testing ? '测试中…' : '测试连接' }}
          </button>
          <button class="primary" :disabled="cards[name].saving" @click="saveService(name)">
            {{ cards[name].saving ? '保存中…' : '保存此服务' }}
          </button>
          <span v-if="cards[name].testState"
                :class="['test-result', { ok: cards[name].testState.ok, bad: !cards[name].testState.ok }]">
            {{ cards[name].testState.ok ? '✓' : '✗' }} {{ cards[name].testState.message }}
          </span>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.settings { display: flex; flex-direction: column; gap: 1rem; }
.page-head { display: flex; align-items: baseline; gap: 1rem; }
h1 { margin: 0; font-size: 1.3rem; }
h2 { margin: 0; font-size: 1.05rem; text-transform: capitalize; }
.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.1rem 1.3rem;
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}
.card-head { display: flex; justify-content: space-between; align-items: center; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.field { display: flex; flex-direction: column; gap: 0.3rem; }
.field > span { font-size: 0.86rem; color: var(--fg-muted); }
.field small { color: var(--fg-muted); }
input[type="text"], input[type="password"], select {
  padding: 0.5rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  font-size: 0.92rem;
  color: var(--fg);
}
input:focus, select:focus { outline: 2px solid var(--accent-soft); border-color: var(--accent); }
.key-row { display: flex; gap: 0.5rem; }
.key-row > input, .key-row > select { flex: 1; }
button {
  padding: 0.45rem 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  font-size: 0.9rem;
}
button:hover:not(:disabled) { background: var(--bg-hover); }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.ghost { white-space: nowrap; }
.actions { display: flex; align-items: center; gap: 0.7rem; flex-wrap: wrap; }
.switch { display: flex; align-items: center; gap: 0.4rem; cursor: pointer; font-size: 0.9rem; }
.switch input { accent-color: var(--accent); width: 1.05rem; height: 1.05rem; }
.dirty { color: #b45309; font-size: 0.85rem; }
.save-msg { font-size: 0.9rem; margin: 0; }
.save-msg.ok { color: var(--ok); }
.save-msg.bad { color: var(--danger); }
.test-result { font-size: 0.88rem; }
.test-result.ok { color: var(--ok); }
.test-result.bad { color: var(--danger); }
.tip { color: var(--fg-muted); padding: 2rem; text-align: center; }
.tip.bad { color: var(--danger); }
</style>
