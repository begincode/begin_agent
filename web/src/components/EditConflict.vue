<script setup lang="ts">
import { ref } from "vue";
defineProps<{ disabled: boolean }>();
defineEmits<{ reload: [] }>();
const confirming = ref(false);
</script>
<template>
  <section class="workspace-panel edit-conflict" role="alert">
    <h2>配置已被其他操作更新</h2>
    <p>
      本页输入仍保留，本次保存未覆盖服务器配置。请先重新加载，再决定如何修改。
    </p>
    <template v-if="confirming">
      <p>重新加载将放弃本页尚未保存的输入，读取服务器最新配置。</p>
      <div class="row-actions">
        <button
          class="primary-button"
          :disabled="disabled"
          @click="$emit('reload')"
        >
          放弃本地修改并加载
        </button>
        <button
          class="secondary-button"
          :disabled="disabled"
          @click="confirming = false"
        >
          保留本地编辑
        </button>
      </div>
    </template>
    <button
      v-else
      class="secondary-button"
      :disabled="disabled"
      @click="confirming = true"
    >
      重新加载配置
    </button>
  </section>
</template>
