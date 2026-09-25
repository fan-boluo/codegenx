import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message, Modal } from 'ant-design-vue';
import { useLoginUserStore } from '@/stores/loginUser';
import { getAppVoById } from '@/api/appController';
import request from '@/request';
import MarkdownRenderer from '@/components/MarkdownRenderer.vue';
import aiAvatar from '@/assets/aiAvatar.png';
import { API_BASE_URL } from '@/config/env';
import { VisualEditor } from '@/utils/visualEditor';
import { VueMonacoEditor } from '@guolao/vue-monaco-editor';
import '@/config/monacoWorkers';
import { CaretRightOutlined, CheckCircleOutlined, CheckSquareOutlined, ClearOutlined, ClockCircleOutlined, CloseCircleOutlined, CloseOutlined, DatabaseOutlined, CodeOutlined, DeleteOutlined, DownOutlined, EditOutlined, FileAddOutlined, FileTextOutlined, FolderAddOutlined, FolderOutlined, HistoryOutlined, LoadingOutlined, MessageOutlined, PlusOutlined, SendOutlined, StopOutlined, UpOutlined, UploadOutlined, } from '@ant-design/icons-vue';
const MAX_OPEN_TABS = 5;
const route = useRoute();
const router = useRouter();
const loginUserStore = useLoginUserStore();
const appInfo = ref({});
const appId = ref();
const sessionId = ref('');
const isCreatingApp = ref(false);
const messages = ref([]);
const userInput = ref('');
const isGenerating = ref(false);
const isStoppingGeneration = ref(false);
const messagesContainer = ref();
const activeGenerationRequestId = ref('');
const activeGenerationSessionId = ref('');
const activeGenerationMessageIndex = ref(null);
const stopRequested = ref(false);
const abortController = ref(null);
// Resizable panels
const sidePanelWidth = ref(260);
const chatWidth = ref(320);
const outputHeight = ref(150);
const inputHeight = ref(100);
const resizing = ref(null);
const outputContainer = ref();
// Monaco editor state
const editorTheme = ref('vs');
const monacoEditor = ref(null);
const editorMounted = ref(false);
const outputLines = ref([]);
const showLoading = computed(() => {
    const tab = activeTabData.value;
    const shouldShow = !!tab?.isLoading;
    if (tab) {
        console.log(`[ShowLoading] Tab: ${tab.path}, isLoading: ${tab.isLoading}, shouldShow: ${shouldShow}`);
    }
    return shouldShow;
});
const LANG_MAP = {
    js: 'javascript', ts: 'typescript', jsx: 'javascript', tsx: 'typescript',
    vue: 'html', html: 'html', css: 'css', scss: 'scss', less: 'less',
    json: 'json', py: 'python', md: 'markdown', yaml: 'yaml', yml: 'yaml',
    sh: 'bash', sql: 'sql', java: 'java', go: 'go', rs: 'rust',
    xml: 'xml', txt: 'plaintext', c: 'c', cpp: 'cpp', h: 'c',
};
const getLanguage = (fileName) => {
    const ext = fileName.split('.').pop()?.toLowerCase() ?? '';
    return LANG_MAP[ext] || 'plaintext';
};
const editorOptions = computed(() => ({
    readOnly: !canOperateApp.value,
    domReadOnly: !canOperateApp.value,
    minimap: { enabled: true },
    fontSize: 13,
    fontFamily: "'SF Mono', 'Fira Code', Menlo, Consolas, monospace",
    lineNumbers: 'on',
    renderWhitespace: 'selection',
    tabSize: 2,
    automaticLayout: true,
    scrollBeyondLastLine: false,
    wordWrap: 'off',
    bracketPairColorization: { enabled: true },
    padding: { top: 8 },
}));
const onResizeStart = (target, e) => {
    resizing.value = target;
    document.addEventListener('mousemove', onResizeMove);
    document.addEventListener('mouseup', onResizeEnd);
    e.preventDefault();
};
const onResizeMove = (e) => {
    if (resizing.value === 'sidebar') {
        const w = Math.max(200, Math.min(400, e.clientX));
        sidePanelWidth.value = w;
    }
    else if (resizing.value === 'chat') {
        const w = Math.max(260, Math.min(600, window.innerWidth - e.clientX));
        chatWidth.value = w;
    }
    else if (resizing.value === 'output') {
        const centerEl = document.querySelector('.center-column');
        if (!centerEl)
            return;
        const rect = centerEl.getBoundingClientRect();
        const h = Math.max(80, Math.min(rect.height * 0.5, rect.bottom - e.clientY));
        outputHeight.value = h;
    }
    else if (resizing.value === 'input') {
        const chatPanel = document.querySelector('.right-chat-panel');
        if (!chatPanel)
            return;
        const rect = chatPanel.getBoundingClientRect();
        const h = Math.max(80, Math.min(300, rect.bottom - e.clientY));
        inputHeight.value = h;
    }
};
const onResizeEnd = () => {
    resizing.value = null;
    document.removeEventListener('mousemove', onResizeMove);
    document.removeEventListener('mouseup', onResizeEnd);
};
const isEditMode = ref(false);
const selectedElementInfo = ref(null);
const visualEditor = new VisualEditor({
    onElementSelected: (elementInfo) => {
        selectedElementInfo.value = elementInfo;
    },
});
const readOnlyTooltip = '无法在别人的项目下操作哦~';
const sidePanelTab = ref('files');
const sidePanelVisible = ref(true);
const sourceFileTree = ref([]);
const sourceRawNodes = ref([]);
const loadingSourceTree = ref(false);
const fileTabs = ref([]);
const activeFileTab = ref('');
const selectedFileNode = ref(null);
const selectedNodeKey = ref('');
const activeTabData = computed(() => {
    return fileTabs.value.find(t => t.path === activeFileTab.value);
});
const dbTables = ref([]);
const loadingDbTables = ref(false);
const dbTableTree = computed(() => {
    return dbTables.value.map((t) => ({
        key: t.name,
        title: t.name,
        isLeaf: true,
    }));
});
const buildSourceTree = (nodes) => {
    return nodes.map((node) => ({
        key: node.path,
        title: node.name,
        isLeaf: node.type === 'file',
        children: node.children ? buildSourceTree(node.children) : undefined,
        dataRef: node,
    }));
};
// Optimistic tree mutations — modify sourceRawNodes then rebuild tree from raw nodes
const rebuildTreeFromRaw = () => {
    sourceFileTree.value = buildSourceTree(sourceRawNodes.value);
};
const addNodeToRaw = (parentPath, newNode) => {
    if (!parentPath) {
        sourceRawNodes.value.push(newNode);
        rebuildTreeFromRaw();
        return;
    }
    const walk = (nodes) => {
        for (const n of nodes) {
            if (n.path === parentPath && n.type === 'dir') {
                if (!n.children)
                    n.children = [];
                n.children.push(newNode);
                return true;
            }
            if (n.children && walk(n.children))
                return true;
        }
        return false;
    };
    walk(sourceRawNodes.value);
    rebuildTreeFromRaw();
};
const removeNodeFromRaw = (targetPath) => {
    const walk = (nodes) => {
        const idx = nodes.findIndex(n => n.path === targetPath);
        if (idx >= 0) {
            nodes.splice(idx, 1);
            return true;
        }
        for (const n of nodes) {
            if (n.children && walk(n.children))
                return true;
        }
        return false;
    };
    walk(sourceRawNodes.value);
    rebuildTreeFromRaw();
};
const renameNodeInRaw = (oldPath, newPath, newName) => {
    const walk = (nodes) => {
        for (const n of nodes) {
            if (n.path === oldPath) {
                n.path = newPath;
                n.name = newName;
                return true;
            }
            if (n.children && walk(n.children))
                return true;
        }
        return false;
    };
    walk(sourceRawNodes.value);
    rebuildTreeFromRaw();
};
const loadSourceTree = async () => {
    if (!appId.value)
        return;
    loadingSourceTree.value = true;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const res = await request.get(`${baseURL}/api/app/code/tree/${appId.value}`);
        if (res.data.code === 0) {
            sourceRawNodes.value = res.data.data || [];
            sourceFileTree.value = buildSourceTree(sourceRawNodes.value);
        }
        else {
            message.error('获取源码目录失败：' + res.data.message);
        }
    }
    catch (error) {
        console.error('获取源码目录失败:', error);
        message.error('获取源码目录失败');
    }
    finally {
        loadingSourceTree.value = false;
    }
};
const loadDbTables = async () => {
    if (!appId.value)
        return;
    loadingDbTables.value = true;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const res = await request.get(`${baseURL}/api/app/db/tables/${appId.value}`);
        if (res.data.code === 0) {
            dbTables.value = res.data.data || [];
        }
        else {
            dbTables.value = [];
        }
    }
    catch (error) {
        console.error('获取数据表失败:', error);
        dbTables.value = [];
    }
    finally {
        loadingDbTables.value = false;
    }
};
const switchSidePanel = (tab) => {
    if (sidePanelTab.value === tab) {
        sidePanelVisible.value = !sidePanelVisible.value;
        return;
    }
    sidePanelTab.value = tab;
    sidePanelVisible.value = true;
    if (tab === 'files' && !sourceFileTree.value.length) {
        loadSourceTree();
    }
    else if (tab === 'data' && !dbTables.value.length) {
        loadDbTables();
    }
};
const switchToFilesTab = async () => {
    sidePanelTab.value = 'files';
    if (!sourceFileTree.value.length) {
        await loadSourceTree();
    }
};
const loadFileContent = async (filePath, signal) => {
    const baseURL = request.defaults.baseURL || API_BASE_URL;
    const res = await request.get(`${baseURL}/api/app/code/file/${appId.value}`, { params: { path: filePath }, signal });
    if (res.data.code === 0) {
        const text = res.data.data ?? '';
        const lines = text.split('\n');
        const isPythonFile = filePath.endsWith('.py');
        return (!isPythonFile && lines.length > 100) ? lines.slice(0, 100).join('\n') : text;
    }
    throw new Error(res.data.message || '读取文件失败');
};
const saveCurrentFile = async () => {
    const tab = activeTabData.value;
    if (!tab || !tab.isDirty || !appId.value)
        return;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const res = await request.post(`${baseURL}/api/app/code/file/${appId.value}`, { content: tab.content }, { params: { path: tab.path } });
        if (res.data.code === 0) {
            tab.originalContent = tab.content;
            tab.isDirty = false;
            message.success('已保存');
        }
        else {
            message.error('保存失败：' + res.data.message);
        }
    }
    catch (error) {
        console.error('保存文件失败:', error);
        message.error('保存失败：' + (error.message || '未知错误'));
    }
};
const handleEditorMount = (editor) => {
    console.log(`[EditorMount] Editor mounted for tab: ${activeFileTab.value}`);
    monacoEditor.value = editor;
    editorMounted.value = true;
    editor.addCommand(
    // Monaco KeyMod.CtrlCmd | Monaco KeyCode.KeyS
    2048 | 49, () => { saveCurrentFile(); });
};
const openFileInTab = async (filePath, fileName) => {
    const existing = fileTabs.value.find(t => t.path === filePath);
    if (existing) {
        existing.lastAccessed = Date.now();
        activeFileTab.value = filePath;
        return;
    }
    if (fileTabs.value.length >= MAX_OPEN_TABS) {
        let oldest = fileTabs.value[0];
        for (let i = 1; i < fileTabs.value.length; i++) {
            if (fileTabs.value[i].lastAccessed < oldest.lastAccessed) {
                oldest = fileTabs.value[i];
            }
        }
        if (oldest.isDirty) {
            message.warning('请先保存或关闭未保存的文件后再打开新文件');
            return;
        }
        const idx = fileTabs.value.indexOf(oldest);
        fileTabs.value.splice(idx, 1);
    }
    const newTab = {
        path: filePath,
        name: fileName,
        content: '',
        originalContent: '',
        isLoading: true,
        lastAccessed: Date.now(),
        isDirty: false,
        language: getLanguage(fileName),
        abortController: null,
    };
    fileTabs.value.push(newTab);
    activeFileTab.value = filePath;
    try {
        const controller = new AbortController();
        newTab.abortController = controller;
        console.log(`[FileLoad] Starting load: ${filePath}`);
        const text = await loadFileContent(filePath, controller.signal);
        console.log(`[FileLoad] Success: ${filePath}, content length: ${text.length}`);
        newTab.content = text;
        newTab.originalContent = text;
    }
    catch (error) {
        console.error(`[FileLoad] Error loading ${filePath}:`, error);
        const errMsg = error?.message || '未知错误';
        newTab.content = `// 加载失败: ${errMsg}\n// 请尝试重新打开此文件`;
        newTab.originalContent = newTab.content;
        message.error('读取文件失败：' + errMsg);
    }
    finally {
        newTab.isLoading = false;
        if (newTab.abortController)
            newTab.abortController = null;
        console.log(`[FileLoad] Finished: ${filePath}, isLoading now false`);
        // Force Vue to detect object property change by replacing the entire object
        const idx = fileTabs.value.findIndex(t => t.path === filePath);
        if (idx >= 0) {
            fileTabs.value[idx] = { ...fileTabs.value[idx] };
            console.log(`[FileLoad] Replaced object at index ${idx}`);
        }
        await nextTick();
        console.log(`[FileLoad] After nextTick, activeTabData.isLoading: ${activeTabData.value?.isLoading}`);
    }
};
const activateFileTab = (path) => {
    const tab = fileTabs.value.find(t => t.path === path);
    if (tab) {
        console.log(`[TabSwitch] Switching to tab: ${path}`);
        tab.lastAccessed = Date.now();
        activeFileTab.value = path;
        console.log(`[TabSwitch] activeFileTab is now: ${activeFileTab.value}`);
    }
};
const closeFileTab = (path) => {
    const idx = fileTabs.value.findIndex(t => t.path === path);
    if (idx === -1)
        return;
    const tab = fileTabs.value[idx];
    // Abort in-flight load if any
    if (tab.abortController) {
        try {
            tab.abortController.abort();
        }
        catch (e) {
            console.warn('abort failed', e);
        }
        tab.abortController = null;
    }
    if (tab.isDirty) {
        Modal.confirm({
            title: '未保存的更改',
            content: `文件 "${tab.name}" 有未保存的更改，确定要关闭吗？`,
            okText: '关闭',
            cancelText: '取消',
            okType: 'danger',
            onOk: () => { doCloseTab(idx, path); },
        });
        return;
    }
    doCloseTab(idx, path);
};
const doCloseTab = (idx, path) => {
    fileTabs.value.splice(idx, 1);
    if (activeFileTab.value === path) {
        if (fileTabs.value.length > 0) {
            let mostRecent = fileTabs.value[0];
            for (const t of fileTabs.value) {
                if (t.lastAccessed > mostRecent.lastAccessed) {
                    mostRecent = t;
                }
            }
            activeFileTab.value = mostRecent.path;
        }
        else {
            activeFileTab.value = '';
        }
    }
};
const handleTreeNodeSelect = (_keys, { node }) => {
    selectedFileNode.value = node;
    selectedNodeKey.value = node.key;
};
const handleSourceFileDoubleClick = async (_e, node) => {
    if (!node.isLeaf)
        return;
    await openFileInTab(node.key, node.title);
};
// File operations
const createFileModalVisible = ref(false);
const createFolderModalVisible = ref(false);
const newItemName = ref('');
const newItemParentPath = ref('');
const fileUploadInput = ref();
const fileOpLoading = ref(false);
// Python env & script running
const selectedPythonEnv = ref('model');
const pythonEnvOptions = [
    { value: 'model', label: 'model' },
];
const runningScript = ref(false);
const runScript = async () => {
    const tab = activeTabData.value;
    if (!tab || !appId.value)
        return;
    if (tab.isDirty) {
        message.warning('请先保存文件再运行');
        return;
    }
    runningScript.value = true;
    outputLines.value = [];
    appendOutput('system', `>>> 运行: python ${tab.name}`);
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const url = `${baseURL}/api/app/code/run/${appId.value}`;
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
            body: JSON.stringify({
                path: tab.path,
                env: selectedPythonEnv.value,
            }),
        });
        if (!response.ok) {
            appendOutput('stderr', `运行失败: HTTP ${response.status}`);
            return;
        }
        const reader = response.body?.getReader();
        if (!reader) {
            appendOutput('stderr', '运行失败: 无响应');
            return;
        }
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done)
                break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed)
                    continue;
                // Strip SSE "data: " prefix
                if (!trimmed.startsWith('data: '))
                    continue;
                const jsonStr = trimmed.slice(6);
                try {
                    const event = JSON.parse(jsonStr);
                    if (event.event_type === 'output') {
                        const text = typeof event.data === 'string' ? event.data : event.data?.text || '';
                        appendOutput(event.data?.stream || 'stdout', text);
                    }
                    else if (event.event_type === 'done') {
                        appendOutput('system', `>>> 脚本结束，退出码: ${event.data?.code ?? 0}`);
                    }
                    else if (event.event_type === 'error') {
                        appendOutput('stderr', event.data?.message || event.data || '未知错误');
                    }
                }
                catch {
                    appendOutput('stdout', trimmed);
                }
            }
        }
        if (buffer.trim() && buffer.trim().startsWith('data: ')) {
            const jsonStr = buffer.trim().slice(6);
            try {
                const event = JSON.parse(jsonStr);
                if (event.event_type === 'done') {
                    appendOutput('system', `>>> 脚本结束，退出码: ${event.data?.code ?? 0}`);
                }
            }
            catch {
                appendOutput('stdout', buffer.trim());
            }
        }
    }
    catch (error) {
        appendOutput('stderr', `运行失败: ${error.message || '未知错误'}`);
    }
    finally {
        runningScript.value = false;
    }
};
const openCreateFileModal = () => {
    const node = selectedFileNode.value;
    newItemParentPath.value = node ? (node.dataRef?.type === 'dir' ? node.key : node.key.substring(0, node.key.lastIndexOf('/') + 1) || '') : '';
    newItemName.value = '';
    createFileModalVisible.value = true;
};
const openCreateFolderModal = () => {
    const node = selectedFileNode.value;
    newItemParentPath.value = node ? (node.dataRef?.type === 'dir' ? node.key : node.key.substring(0, node.key.lastIndexOf('/') + 1) || '') : '';
    newItemName.value = '';
    createFolderModalVisible.value = true;
};
const doCreateFile = async () => {
    if (!newItemName.value.trim())
        return;
    fileOpLoading.value = true;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const filePath = newItemParentPath.value ? `${newItemParentPath.value}/${newItemName.value.trim()}` : newItemName.value.trim();
        const res = await request.post(`${baseURL}/api/app/code/file/create/${appId.value}`, null, {
            params: { path: filePath },
        });
        if (res.data.code === 0) {
            message.success('文件创建成功');
            createFileModalVisible.value = false;
            addNodeToRaw(newItemParentPath.value, { path: filePath, name: newItemName.value.trim(), type: 'file' });
        }
        else {
            message.error('创建失败：' + res.data.message);
        }
    }
    catch (error) {
        message.error('创建失败：' + (error.message || '未知错误'));
    }
    finally {
        fileOpLoading.value = false;
    }
};
const doCreateFolder = async () => {
    if (!newItemName.value.trim())
        return;
    fileOpLoading.value = true;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const folderPath = newItemParentPath.value ? `${newItemParentPath.value}/${newItemName.value.trim()}` : newItemName.value.trim();
        const res = await request.post(`${baseURL}/api/app/code/folder/create/${appId.value}`, null, {
            params: { path: folderPath },
        });
        if (res.data.code === 0) {
            message.success('文件夹创建成功');
            createFolderModalVisible.value = false;
            addNodeToRaw(newItemParentPath.value, { path: folderPath, name: newItemName.value.trim(), type: 'dir', children: [] });
        }
        else {
            message.error('创建失败：' + res.data.message);
        }
    }
    catch (error) {
        message.error('创建失败：' + (error.message || '未知错误'));
    }
    finally {
        fileOpLoading.value = false;
    }
};
// Rename
const renameModalVisible = ref(false);
const renameNewName = ref('');
const openRenameModal = () => {
    const node = selectedFileNode.value;
    if (!node)
        return;
    renameNewName.value = node.title;
    renameModalVisible.value = true;
};
const doRename = async () => {
    const node = selectedFileNode.value;
    if (!node || !renameNewName.value.trim() || renameNewName.value.trim() === node.title) {
        renameModalVisible.value = false;
        return;
    }
    fileOpLoading.value = true;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const parentPath = node.key.includes('/') ? node.key.substring(0, node.key.lastIndexOf('/') + 1) : '';
        const newPath = parentPath ? `${parentPath}${renameNewName.value.trim()}` : renameNewName.value.trim();
        const res = await request.post(`${baseURL}/api/app/code/rename/${appId.value}`, null, {
            params: { from: node.key, to: newPath },
        });
        if (res.data.code === 0) {
            message.success('重命名成功');
            renameModalVisible.value = false;
            renameNodeInRaw(node.key, newPath, renameNewName.value.trim());
            // Close old tab for this file
            const oldTabIdx = fileTabs.value.findIndex(t => t.path === node.key);
            if (oldTabIdx >= 0) {
                fileTabs.value.splice(oldTabIdx, 1);
                if (activeFileTab.value === node.key) {
                    activeFileTab.value = fileTabs.value.length > 0 ? fileTabs.value[Math.min(oldTabIdx, fileTabs.value.length - 1)].path : '';
                }
            }
            selectedFileNode.value = null;
            await loadSourceTree();
        }
        else {
            message.error('重命名失败：' + res.data.message);
        }
    }
    catch (error) {
        message.error('重命名失败：' + (error.message || '未知错误'));
    }
    finally {
        fileOpLoading.value = false;
    }
};
const triggerFileUpload = () => {
    if (!fileUploadInput.value) {
        const input = document.createElement('input');
        input.type = 'file';
        input.style.display = 'none';
        input.addEventListener('change', handleFileUpload);
        document.body.appendChild(input);
        fileUploadInput.value = input;
    }
    fileUploadInput.value.click();
};
const handleFileUpload = async (e) => {
    const target = e.target;
    const file = target.files?.[0];
    if (!file)
        return;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const node = selectedFileNode.value;
        const parentPath = node ? (node.dataRef?.type === 'dir' ? node.key : node.key.substring(0, node.key.lastIndexOf('/') + 1) || '') : '';
        const filePath = parentPath ? `${parentPath}/${file.name}` : file.name;
        const formData = new FormData();
        formData.append('file', file);
        const res = await request.post(`${baseURL}/api/app/code/file/upload/${appId.value}`, formData, {
            params: { path: filePath },
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        if (res.data.code === 0) {
            message.success('上传成功');
            await loadSourceTree();
        }
        else {
            message.error('上传失败：' + res.data.message);
        }
    }
    catch (error) {
        message.error('上传失败：' + (error.message || '未知错误'));
    }
    finally {
        target.value = '';
    }
};
const deleteSelectedNode = () => {
    const node = selectedFileNode.value;
    if (!node)
        return;
    const name = node.title;
    const isDir = node.dataRef?.type === 'dir';
    Modal.confirm({
        title: isDir ? '删除文件夹' : '删除文件',
        content: `确定要删除「${name}」吗？${isDir ? '将同时删除文件夹内的所有内容。' : ''}此操作不可撤销。`,
        okText: '删除',
        okType: 'danger',
        cancelText: '取消',
        onOk: async () => {
            try {
                const baseURL = request.defaults.baseURL || API_BASE_URL;
                const res = await request.delete(`${baseURL}/api/app/code/${appId.value}`, {
                    params: { path: node.key },
                });
                if (res.data.code === 0) {
                    message.success('删除成功');
                    // Close all tabs that are in/under the deleted path
                    const deletedPath = node.key;
                    const deletedIsDir = node.dataRef?.type === 'dir';
                    // Abort any in-flight loads for affected tabs
                    for (const t of fileTabs.value) {
                        if (t.path === deletedPath || (deletedIsDir && t.path.startsWith(deletedPath + '/'))) {
                            if (t.abortController) {
                                try {
                                    t.abortController.abort();
                                }
                                catch (e) { /* ignore */ }
                                t.abortController = null;
                            }
                        }
                    }
                    fileTabs.value = fileTabs.value.filter(t => {
                        if (t.path === deletedPath)
                            return false;
                        if (deletedIsDir && t.path.startsWith(deletedPath + '/'))
                            return false;
                        return true;
                    });
                    if (activeFileTab.value && !fileTabs.value.find(t => t.path === activeFileTab.value)) {
                        activeFileTab.value = fileTabs.value.length > 0 ? fileTabs.value[fileTabs.value.length - 1].path : '';
                    }
                    selectedFileNode.value = null;
                    await loadSourceTree();
                }
                else {
                    message.error('删除失败：' + res.data.message);
                }
            }
            catch (error) {
                message.error('删除失败：' + (error.message || '未知错误'));
            }
        },
    });
};
const clearOutput = () => {
    outputLines.value = [];
};
const appendOutput = (type, text) => {
    outputLines.value.push({ type, text, timestamp: Date.now() });
    if (outputContainer.value) {
        outputContainer.value.scrollTop = outputContainer.value.scrollHeight;
    }
};
const getAuthHeaders = () => {
    const token = localStorage.getItem('token');
    if (!token)
        return {};
    return { Authorization: `Bearer ${token}` };
};
const createClientId = () => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
};
const getAppSessionStorageKey = (currentAppId) => `codegenx:app-chat-session:${currentAppId}`;
const clearChatSessionId = () => { sessionId.value = ''; };
const sessionHistoryVisible = ref(false);
const loadingSessionHistory = ref(false);
const sessionList = ref([]);
const loadingMessages = ref(false);
const createNewSession = () => {
    if (!appId.value)
        return;
    const storageKey = getAppSessionStorageKey(appId.value);
    localStorage.removeItem(storageKey);
    sessionId.value = createClientId();
    localStorage.setItem(storageKey, sessionId.value);
    messages.value = [];
    tasks.value = [];
    message.info('已创建新会话');
};
const toggleSessionHistory = async () => {
    if (!appId.value)
        return;
    if (sessionHistoryVisible.value) {
        sessionHistoryVisible.value = false;
        return;
    }
    sessionHistoryVisible.value = true;
    loadingSessionHistory.value = true;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const res = await fetch(`${baseURL}/api/ai/sessions/${appId.value}?limit=5`, {
            headers: { ...getAuthHeaders() },
        });
        if (res.ok) {
            const result = await res.json();
            sessionList.value = result.data || [];
        }
    }
    catch (e) {
        console.error('获取会话历史失败', e);
    }
    finally {
        loadingSessionHistory.value = false;
    }
};
/**
 * 将 API 返回的 JSONL 消息转换为前端 MessageItem[]。
 * tool 角色消息合并入其前的 assistant 消息 items 中。
 */
const convertMessagesFromApi = (msgs) => {
    const items = [];
    for (const m of msgs) {
        if (m.role === 'user') {
            items.push({ type: 'user', content: m.content || '', createTime: m.create_time });
        }
        else if (m.role === 'assistant') {
            if (m.content) {
                items.push({ type: 'ai', content: m.content, createTime: m.create_time });
            }
        }
        else if (m.role === 'tool') {
            const toolName = m.name || 'tool';
            const shortContent = (m.content || '').length > 200
                ? (m.content || '').substring(0, 200) + '...'
                : (m.content || '');
            items.push({
                type: 'ai',
                content: '',
                createTime: m.create_time,
                items: [{
                        type: 'tool',
                        toolResult: { name: toolName, detail: shortContent },
                    }],
            });
        }
    }
    return items;
};
const loadSession = async (sid) => {
    if (!appId.value)
        return;
    loadingMessages.value = true;
    sessionHistoryVisible.value = false;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const res = await fetch(`${baseURL}/api/ai/sessions/${appId.value}/${sid}/messages?limit=50`, {
            headers: { ...getAuthHeaders() },
        });
        if (!res.ok)
            throw new Error('load failed');
        const result = await res.json();
        const msgs = result.data || [];
        messages.value = convertMessagesFromApi(msgs);
        // Update localStorage with loaded session id
        const storageKey = getAppSessionStorageKey(appId.value);
        localStorage.setItem(storageKey, sid);
        sessionId.value = sid;
        message.success('已加载历史会话');
    }
    catch (e) {
        console.error('加载会话消息失败', e);
        message.error('加载会话消息失败');
    }
    finally {
        loadingMessages.value = false;
    }
};
const loadLatestSessionMessages = async () => {
    if (!appId.value)
        return;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        // 获取最近的会话列表
        const sessionsRes = await fetch(`${baseURL}/api/ai/sessions/${appId.value}?limit=1`, {
            headers: { ...getAuthHeaders() },
        });
        if (!sessionsRes.ok)
            return;
        const result = await sessionsRes.json();
        const sessions = result.data || [];
        if (!sessions.length)
            return;
        const latestSessionId = sessions[0].session_id;
        // 有历史会话则加载消息
        await loadSession(latestSessionId);
    }
    catch (e) {
        console.error('加载最近会话失败', e);
    }
};
const checkSessionAlive = async (sid) => {
    if (!appId.value)
        return false;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const res = await fetch(`${baseURL}/api/ai/sessions/${appId.value}/${sid}/alive`, {
            headers: { ...getAuthHeaders() },
        });
        if (!res.ok)
            return false;
        const result = await res.json();
        return result.data?.alive === true;
    }
    catch {
        return false;
    }
};
const ensureChatSessionId = (currentAppId) => {
    if (!currentAppId) {
        clearChatSessionId();
        return '';
    }
    const storageKey = getAppSessionStorageKey(currentAppId);
    const storedSessionId = localStorage.getItem(storageKey)?.trim();
    if (storedSessionId) {
        sessionId.value = storedSessionId;
        return storedSessionId;
    }
    const createdSessionId = createClientId();
    localStorage.setItem(storageKey, createdSessionId);
    sessionId.value = createdSessionId;
    return createdSessionId;
};
const getMessageAt = (index) => messages.value[index];
const applyMessageChunk = (chunk) => {
    if (chunk === undefined || chunk === null)
        return;
    const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1);
    if (!targetMessage)
        return;
    targetMessage.content = `${targetMessage.content || ''}${String(chunk)}`;
    if (!targetMessage.items)
        targetMessage.items = [];
    const last = targetMessage.items[targetMessage.items.length - 1];
    if (last && last.type === 'text') {
        last.text = (last.text || '') + String(chunk);
    }
    else {
        targetMessage.items.push({ type: 'text', text: String(chunk) });
    }
    targetMessage.loading = false;
    scrollToBottom();
};
const finishStream = () => {
    isGenerating.value = false;
    isStoppingGeneration.value = false;
    stopRequested.value = false;
    clearActiveGeneration(activeGenerationRequestId.value);
    setTimeout(async () => { await refreshAfterGeneration(); }, 1000);
};
const refreshAfterGeneration = async () => {
    if (hadFileChangeInGeneration.value) {
        hadFileChangeInGeneration.value = false;
        await loadSourceTree();
    }
};
const appendAiStep = (step) => {
    const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1);
    if (!targetMessage)
        return;
    if (!targetMessage.steps)
        targetMessage.steps = [];
    targetMessage.steps.push(step);
    if (!targetMessage.items)
        targetMessage.items = [];
    targetMessage.items.push({ type: 'step', step });
    scrollToBottom();
};
const updateLastRunningToolStep = (detail, state, toolId) => {
    const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1);
    if (!targetMessage?.steps)
        return;
    for (let i = targetMessage.steps.length - 1; i >= 0; i--) {
        if (targetMessage.steps[i].eventType === 'ToolExecutionStart' && targetMessage.steps[i].state === 'running') {
            if (toolId && targetMessage.steps[i].toolId && targetMessage.steps[i].toolId !== toolId)
                continue;
            targetMessage.steps[i].state = state;
            targetMessage.steps[i].detail = detail;
            scrollToBottom();
            return;
        }
    }
};
const failAllRunningToolSteps = () => {
    const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1);
    if (!targetMessage?.steps)
        return;
    for (const step of targetMessage.steps) {
        if (step.eventType === 'ToolExecutionStart' && step.state === 'running') {
            step.state = 'failed';
            step.detail = '执行中断';
        }
    }
};
const handleStreamEvent = (event) => {
    const eventType = event.event_type;
    const eventData = event.data;
    if (eventType === 'LLM_Response_Chunk') {
        applyMessageChunk(eventData);
        return;
    }
    if (eventType === 'OnTurnStart') {
        appendAiStep({ eventType, description: '开始处理', detail: '', state: 'completed', timestamp: Date.now() });
        return;
    }
    if (eventType === 'LLM_Thinking_Start') {
        const promptTokens = eventData?.prompt_tokens ?? 0;
        const messageCount = eventData?.message_count ?? 0;
        appendAiStep({ eventType, description: 'AI 思考中', detail: `${promptTokens} tokens, ${messageCount} 条消息`, state: 'completed', timestamp: Date.now() });
        return;
    }
    if (eventType === 'ToolExecutionStart') {
        const toolName = eventData?.tool_name || eventData?.name || eventData?.function?.name || 'unknown';
        appendAiStep({ eventType, description: `${toolName}`, detail: '', state: 'running', timestamp: Date.now(), toolId: eventData?.tool_id || '' });
        return;
    }
    if (eventType === 'ToolExecutionEnd') {
        const toolName = eventData?.tool_name || eventData?.name || 'unknown';
        const description = eventData?.description || '';
        const toolState = eventData?.state || '';
        const isSuccess = toolState === 'success';
        const detail = isSuccess ? description : (description || `失败: ${toolName}`);
        const toolId = eventData?.tool_id || '';
        // 更新任务面板
        if (eventData?.task_data) {
            const td = eventData.task_data;
            if (td.action === 'create') {
                tasks.value.push(td.task);
            }
            else if (td.action === 'update') {
                const existingIdx = tasks.value.findIndex(t => t.id === td.task.id);
                if (existingIdx >= 0) {
                    tasks.value[existingIdx] = { ...tasks.value[existingIdx], ...td.task };
                }
            }
        }
        const FILE_MODIFY_TOOLS = new Set(['write_file', 'edit_file', 'delete_file', 'bash', 'subagent']);
        if (isSuccess && FILE_MODIFY_TOOLS.has(toolName)) {
            hadFileChangeInGeneration.value = true;
        }
        updateLastRunningToolStep(detail, isSuccess ? 'completed' : 'failed', toolId);
        return;
    }
    if (eventType === 'CompactEvent') {
        const tokensBefore = eventData?.tokens_before ?? 0;
        const tokensAfter = eventData?.tokens_after ?? 0;
        const removed = eventData?.messages_removed ?? 0;
        appendAiStep({ eventType, description: '上下文压缩', detail: `tokens: ${tokensBefore} → ${tokensAfter}, 移除 ${removed} 条`, state: 'completed', timestamp: Date.now() });
        return;
    }
    if (eventType === 'RequestCompleted' || eventType === 'RequestStopped') {
        if (stopRequested.value) {
            const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1);
            if (targetMessage && !targetMessage.content) {
                targetMessage.content = '已停止本次生成。';
            }
        }
        finishStream();
        return;
    }
    if (eventType === 'Error') {
        failAllRunningToolSteps();
        const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1);
        if (targetMessage) {
            targetMessage.content = '执行出错，请稍后重试';
            targetMessage.loading = false;
        }
        message.error('执行出错，请稍后重试');
        finishStream();
        return;
    }
    if (eventType === 'Log_Chunk' || eventType === 'Terminal_Output') {
        const text = typeof eventData === 'string' ? eventData : eventData?.text || JSON.stringify(eventData);
        appendOutput('stdout', text);
        return;
    }
    console.debug('收到未处理的事件:', eventType, eventData);
};
// Reset editor mounted flag when switching tabs (key changes, Monaco recreated)
watch(activeFileTab, () => {
    console.log(`[EditorLifecycle] activeFileTab changed to: ${activeFileTab.value}`);
    editorMounted.value = false;
    monacoEditor.value = null;
});
// Watch for content changes to track dirty state
watch(() => fileTabs.value.map(t => ({ path: t.path, content: t.content })), () => {
    for (const tab of fileTabs.value) {
        if (tab.originalContent !== undefined) {
            tab.isDirty = tab.content !== tab.originalContent;
        }
    }
}, { deep: true });
// 后端 get/vo 已校验参与者身份（owner/成员/管理员），能加载出项目即可操作
const canOperateApp = computed(() => Boolean(appId.value));
// Task board state
const tasks = ref([]);
const taskBoardCollapsed = ref(false);
const visibleTasks = computed(() => tasks.value.filter(t => t.status !== 'deleted'));
// Track file-modifying tools for intelligent tree refresh
const hadFileChangeInGeneration = ref(false);
const clearActiveGeneration = (requestId) => {
    if (requestId && activeGenerationRequestId.value && activeGenerationRequestId.value !== requestId)
        return;
    activeGenerationRequestId.value = '';
    activeGenerationSessionId.value = '';
    activeGenerationMessageIndex.value = null;
    stopRequested.value = false;
    isStoppingGeneration.value = false;
};
const fetchAppInfo = async (options) => {
    const id = options?.appId ?? route.params.id;
    if (!id) {
        appId.value = undefined;
        clearChatSessionId();
        appInfo.value = {};
        messages.value = [];
        sourceFileTree.value = [];
        return;
    }
    appId.value = id;
    ensureChatSessionId(id);
    try {
        const res = await getAppVoById({ id: id });
        if (res.data.code === 0 && res.data.data) {
            appInfo.value = res.data.data;
            if (messages.value.length >= 2) { /* generated */ }
            if (messages.value.length === 0) {
                // 尝试恢复本地存储的 session
                const storageKey = getAppSessionStorageKey(id);
                const storedSid = localStorage.getItem(storageKey)?.trim();
                if (storedSid && await checkSessionAlive(storedSid)) {
                    // session 还活着 → 加载历史，接着聊
                    await loadSession(storedSid);
                }
                else {
                    // session 已过期或不存在 → 清理旧ID，新session
                    if (storedSid)
                        localStorage.removeItem(storageKey);
                    const newSid = createClientId();
                    localStorage.setItem(storageKey, newSid);
                    sessionId.value = newSid;
                }
            }
            await loadSourceTree();
            await loadDbTables();
            return;
        }
        message.error('获取项目信息失败');
        router.push('/');
    }
    catch (error) {
        console.error('获取项目信息失败：', error);
        message.error('获取项目信息失败');
        router.push('/');
    }
};
const sendMessage = async () => {
    if (!canOperateApp.value || !userInput.value.trim() || isGenerating.value || isCreatingApp.value)
        return;
    let outgoingMessage = userInput.value.trim();
    if (selectedElementInfo.value) {
        let elementContext = `\n\n选中元素信息：`;
        if (selectedElementInfo.value.pagePath) {
            elementContext += `\n- 页面路径: ${selectedElementInfo.value.pagePath}`;
        }
        elementContext += `\n- 标签: ${selectedElementInfo.value.tagName.toLowerCase()}\n- 选择器: ${selectedElementInfo.value.selector}`;
        if (selectedElementInfo.value.textContent) {
            elementContext += `\n- 当前内容: ${selectedElementInfo.value.textContent.substring(0, 100)}`;
        }
        outgoingMessage += elementContext;
    }
    if (!appId.value) {
        message.info('请先创建项目再开始对话');
        return;
    }
    userInput.value = '';
    messages.value.push({ type: 'user', content: outgoingMessage });
    if (selectedElementInfo.value) {
        clearSelectedElement();
        if (isEditMode.value) {
            toggleEditMode();
        }
    }
    const aiMessageIndex = messages.value.length;
    messages.value.push({ type: 'ai', content: '', loading: true });
    await nextTick();
    scrollToBottom();
    isGenerating.value = true;
    await generateCode(outgoingMessage, aiMessageIndex);
};
const generateCode = async (userMessage, aiMessageIndex) => {
    const currentSessionId = ensureChatSessionId(appId.value);
    const requestId = createClientId();
    activeGenerationRequestId.value = requestId;
    activeGenerationSessionId.value = currentSessionId;
    activeGenerationMessageIndex.value = aiMessageIndex;
    stopRequested.value = false;
    isStoppingGeneration.value = false;
    const controller = new AbortController();
    abortController.value = controller;
    try {
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        const url = `${baseURL}/api/ai/chat/gen`;
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
            body: JSON.stringify({ appId: Number(appId.value), message: userMessage, sessionId: currentSessionId, requestId, traceId: createClientId(), dbName: appInfo.value?.dbName || null }),
            signal: controller.signal,
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const reader = response.body?.getReader();
        if (!reader) {
            throw new Error('No response body');
        }
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done)
                break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed)
                    continue;
                try {
                    const event = JSON.parse(trimmed);
                    if (event.event_type) {
                        handleStreamEvent(event);
                    }
                }
                catch { /* skip */ }
            }
        }
        if (buffer.trim()) {
            try {
                const event = JSON.parse(buffer.trim());
                if (event.event_type) {
                    handleStreamEvent(event);
                }
            }
            catch { /* skip */ }
        }
        finishStream();
    }
    catch (error) {
        if (error.name === 'AbortError') {
            return;
        }
        handleError(error, aiMessageIndex, requestId);
    }
    finally {
        if (abortController.value === controller) {
            abortController.value = null;
        }
    }
};
const handleError = (error, aiMessageIndex, requestId) => {
    console.error('执行失败：', error);
    const targetMessage = getMessageAt(aiMessageIndex);
    if (targetMessage) {
        targetMessage.content = '抱歉，执行中出现了错误，请重试。';
        targetMessage.loading = false;
    }
    message.error('执行失败，请重试');
    isGenerating.value = false;
    clearActiveGeneration(requestId);
};
const stopGeneration = async () => {
    if (!isGenerating.value || isStoppingGeneration.value || !appId.value || !activeGenerationRequestId.value || !activeGenerationSessionId.value)
        return;
    isStoppingGeneration.value = true;
    stopRequested.value = true;
    try {
        abortController.value?.abort();
        const baseURL = request.defaults.baseURL || API_BASE_URL;
        await fetch(`${baseURL}/api/ai/chat/stop`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
            body: JSON.stringify({ appId: Number(appId.value), sessionId: activeGenerationSessionId.value, requestId: activeGenerationRequestId.value, traceId: createClientId(), reason: 'user-stop' }),
        });
        message.info('已停止生成');
    }
    catch (error) {
        console.error('停止请求失败：', error);
    }
    finally {
        finishStream();
    }
};
const scrollToBottom = () => { if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
} };
const toggleEditMode = () => {
    if (!canOperateApp.value) {
        message.warning(readOnlyTooltip);
        return;
    }
    message.info('编辑模式需要网页预览，请先点击"预览网页"在新窗口打开后再编辑');
};
const clearSelectedElement = () => { selectedElementInfo.value = null; visualEditor.clearSelection(); };
const getInputPlaceholder = () => {
    if (!appId.value)
        return '请先创建项目，再开始对话';
    if (!canOperateApp.value)
        return readOnlyTooltip;
    if (selectedElementInfo.value)
        return `正在编辑 ${selectedElementInfo.value.tagName.toLowerCase()} 元素，描述您想要的修改...`;
    return '请描述你的需求，越详细效果越好哦';
};
// Route navigation guard for unsaved changes
onBeforeUnmount(() => {
    const dirtyTabs = fileTabs.value.filter(t => t.isDirty);
    if (dirtyTabs.length > 0) {
        const names = dirtyTabs.map(t => t.name).join(', ');
        const leave = window.confirm(`以下文件有未保存的更改：\n${names}\n\n确定要离开吗？`);
        if (!leave) {
            throw new Error('Navigation blocked');
        }
    }
});
watch(() => route.params.id, async (newId, oldId) => {
    if (newId === oldId)
        return;
    fileTabs.value = [];
    activeFileTab.value = '';
    sidePanelTab.value = 'files';
    sidePanelVisible.value = true;
    await fetchAppInfo({ appId: typeof newId === 'string' ? newId : undefined });
});
onMounted(() => {
    sidePanelTab.value = 'files';
    fetchAppInfo();
});
onBeforeUnmount(() => {
    abortController.value?.abort();
    document.removeEventListener('mousemove', onResizeMove);
    document.removeEventListener('mouseup', onResizeEnd);
});
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['activity-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['activity-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['activity-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['side-panel-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['side-panel-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['ant-btn-text']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-empty']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-tree']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-tree']} */ ;
/** @type {__VLS_StyleScopedClasses['ant-tree-node-content-wrapper']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-tree']} */ ;
/** @type {__VLS_StyleScopedClasses['resizer']} */ ;
/** @type {__VLS_StyleScopedClasses['resizer']} */ ;
/** @type {__VLS_StyleScopedClasses['file-tab-bar-left']} */ ;
/** @type {__VLS_StyleScopedClasses['file-tab']} */ ;
/** @type {__VLS_StyleScopedClasses['file-tab']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['file-tab']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['file-tab']} */ ;
/** @type {__VLS_StyleScopedClasses['file-tab-close']} */ ;
/** @type {__VLS_StyleScopedClasses['file-tab-close']} */ ;
/** @type {__VLS_StyleScopedClasses['resizer-horizontal']} */ ;
/** @type {__VLS_StyleScopedClasses['resizer-horizontal']} */ ;
/** @type {__VLS_StyleScopedClasses['output-header']} */ ;
/** @type {__VLS_StyleScopedClasses['ant-btn-text']} */ ;
/** @type {__VLS_StyleScopedClasses['output-header']} */ ;
/** @type {__VLS_StyleScopedClasses['ant-btn-text']} */ ;
/** @type {__VLS_StyleScopedClasses['session-item']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-content']} */ ;
/** @type {__VLS_StyleScopedClasses['message-avatar']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step']} */ ;
/** @type {__VLS_StyleScopedClasses['step-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step']} */ ;
/** @type {__VLS_StyleScopedClasses['step-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step']} */ ;
/** @type {__VLS_StyleScopedClasses['step-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step']} */ ;
/** @type {__VLS_StyleScopedClasses['step-running']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step-inline']} */ ;
/** @type {__VLS_StyleScopedClasses['step-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step-inline']} */ ;
/** @type {__VLS_StyleScopedClasses['step-running']} */ ;
/** @type {__VLS_StyleScopedClasses['step-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step-inline']} */ ;
/** @type {__VLS_StyleScopedClasses['step-completed']} */ ;
/** @type {__VLS_StyleScopedClasses['step-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step-inline']} */ ;
/** @type {__VLS_StyleScopedClasses['step-failed']} */ ;
/** @type {__VLS_StyleScopedClasses['step-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step-inline']} */ ;
/** @type {__VLS_StyleScopedClasses['step-running']} */ ;
/** @type {__VLS_StyleScopedClasses['step-desc']} */ ;
/** @type {__VLS_StyleScopedClasses['ai-step-inline']} */ ;
/** @type {__VLS_StyleScopedClasses['step-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-result-inline']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-result-inline']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-result-inline']} */ ;
/** @type {__VLS_StyleScopedClasses['task-board-header']} */ ;
/** @type {__VLS_StyleScopedClasses['task-board-title']} */ ;
/** @type {__VLS_StyleScopedClasses['task-chip']} */ ;
/** @type {__VLS_StyleScopedClasses['task-chip']} */ ;
/** @type {__VLS_StyleScopedClasses['task-chip']} */ ;
/** @type {__VLS_StyleScopedClasses['task-completed']} */ ;
/** @type {__VLS_StyleScopedClasses['task-chip-indicator']} */ ;
/** @type {__VLS_StyleScopedClasses['task-chip']} */ ;
/** @type {__VLS_StyleScopedClasses['task-in_progress']} */ ;
/** @type {__VLS_StyleScopedClasses['task-chip-indicator']} */ ;
/** @type {__VLS_StyleScopedClasses['task-chip']} */ ;
/** @type {__VLS_StyleScopedClasses['task-chip-indicator']} */ ;
/** @type {__VLS_StyleScopedClasses['input-resizer']} */ ;
/** @type {__VLS_StyleScopedClasses['input-resizer']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-input']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-input']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-input']} */ ;
/** @type {__VLS_StyleScopedClasses['input-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['right-chat-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['side-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['side-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['side-panel-title']} */ ;
/** @type {__VLS_StyleScopedClasses['side-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['side-panel-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['side-panel-header']} */ ;
/** @type {__VLS_StyleScopedClasses['right-chat-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['ide-layout']} */ ;
/** @type {__VLS_StyleScopedClasses['message-bubble']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    id: "appChatPage",
    ...{ class: "ide-layout" },
});
/** @type {__VLS_StyleScopedClasses['ide-layout']} */ ;
if (__VLS_ctx.appId) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "activity-bar" },
    });
    /** @type {__VLS_StyleScopedClasses['activity-bar']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "activity-bar-top" },
    });
    /** @type {__VLS_StyleScopedClasses['activity-bar-top']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.appId))
                    return;
                __VLS_ctx.switchSidePanel('files');
                // @ts-ignore
                [appId, switchSidePanel,];
            } },
        ...{ class: "activity-icon" },
        ...{ class: ({ active: __VLS_ctx.sidePanelTab === 'files' }) },
        title: "项目文件",
    });
    /** @type {__VLS_StyleScopedClasses['activity-icon']} */ ;
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    let __VLS_0;
    /** @ts-ignore @type { | typeof __VLS_components.FolderOutlined} */
    FolderOutlined;
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({}));
    const __VLS_2 = __VLS_1({}, ...__VLS_functionalComponentArgsRest(__VLS_1));
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.appId))
                    return;
                __VLS_ctx.switchSidePanel('data');
                // @ts-ignore
                [switchSidePanel, sidePanelTab,];
            } },
        ...{ class: "activity-icon" },
        ...{ class: ({ active: __VLS_ctx.sidePanelTab === 'data' }) },
        title: "数据表",
    });
    /** @type {__VLS_StyleScopedClasses['activity-icon']} */ ;
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    let __VLS_5;
    /** @ts-ignore @type { | typeof __VLS_components.DatabaseOutlined} */
    DatabaseOutlined;
    // @ts-ignore
    const __VLS_6 = __VLS_asFunctionalComponent1(__VLS_5, new __VLS_5({}));
    const __VLS_7 = __VLS_6({}, ...__VLS_functionalComponentArgsRest(__VLS_6));
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "activity-bar-bottom" },
    });
    /** @type {__VLS_StyleScopedClasses['activity-bar-bottom']} */ ;
}
if (__VLS_ctx.appId && __VLS_ctx.sidePanelVisible) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "side-panel" },
        ...{ style: ({ width: __VLS_ctx.sidePanelWidth + 'px' }) },
    });
    /** @type {__VLS_StyleScopedClasses['side-panel']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "side-panel-header" },
    });
    /** @type {__VLS_StyleScopedClasses['side-panel-header']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "side-panel-title" },
    });
    /** @type {__VLS_StyleScopedClasses['side-panel-title']} */ ;
    if (__VLS_ctx.sidePanelTab === 'files') {
        let __VLS_10;
        /** @ts-ignore @type { | typeof __VLS_components.FolderOutlined} */
        FolderOutlined;
        // @ts-ignore
        const __VLS_11 = __VLS_asFunctionalComponent1(__VLS_10, new __VLS_10({}));
        const __VLS_12 = __VLS_11({}, ...__VLS_functionalComponentArgsRest(__VLS_11));
    }
    else {
        let __VLS_15;
        /** @ts-ignore @type { | typeof __VLS_components.DatabaseOutlined} */
        DatabaseOutlined;
        // @ts-ignore
        const __VLS_16 = __VLS_asFunctionalComponent1(__VLS_15, new __VLS_15({}));
        const __VLS_17 = __VLS_16({}, ...__VLS_functionalComponentArgsRest(__VLS_16));
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (__VLS_ctx.sidePanelTab === 'files' ? '项目文件' : '数据表');
    if (__VLS_ctx.sidePanelTab === 'files' && __VLS_ctx.canOperateApp) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ class: "side-panel-actions" },
        });
        /** @type {__VLS_StyleScopedClasses['side-panel-actions']} */ ;
        let __VLS_20;
        /** @ts-ignore @type { | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip'] | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip']} */
        aTooltip;
        // @ts-ignore
        const __VLS_21 = __VLS_asFunctionalComponent1(__VLS_20, new __VLS_20({
            title: "新建文件",
        }));
        const __VLS_22 = __VLS_21({
            title: "新建文件",
        }, ...__VLS_functionalComponentArgsRest(__VLS_21));
        const { default: __VLS_25 } = __VLS_23.slots;
        let __VLS_26;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_27 = __VLS_asFunctionalComponent1(__VLS_26, new __VLS_26({
            ...{ 'onClick': {} },
            type: "text",
            size: "small",
        }));
        const __VLS_28 = __VLS_27({
            ...{ 'onClick': {} },
            type: "text",
            size: "small",
        }, ...__VLS_functionalComponentArgsRest(__VLS_27));
        let __VLS_31;
        const __VLS_32 = ({ click: {} },
            { onClick: (__VLS_ctx.openCreateFileModal) });
        const { default: __VLS_33 } = __VLS_29.slots;
        {
            const { icon: __VLS_34 } = __VLS_29.slots;
            let __VLS_35;
            /** @ts-ignore @type { | typeof __VLS_components.FileAddOutlined} */
            FileAddOutlined;
            // @ts-ignore
            const __VLS_36 = __VLS_asFunctionalComponent1(__VLS_35, new __VLS_35({}));
            const __VLS_37 = __VLS_36({}, ...__VLS_functionalComponentArgsRest(__VLS_36));
            // @ts-ignore
            [appId, sidePanelTab, sidePanelTab, sidePanelTab, sidePanelTab, sidePanelVisible, sidePanelWidth, canOperateApp, openCreateFileModal,];
        }
        // @ts-ignore
        [];
        var __VLS_29;
        var __VLS_30;
        // @ts-ignore
        [];
        var __VLS_23;
        let __VLS_40;
        /** @ts-ignore @type { | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip'] | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip']} */
        aTooltip;
        // @ts-ignore
        const __VLS_41 = __VLS_asFunctionalComponent1(__VLS_40, new __VLS_40({
            title: "新建文件夹",
        }));
        const __VLS_42 = __VLS_41({
            title: "新建文件夹",
        }, ...__VLS_functionalComponentArgsRest(__VLS_41));
        const { default: __VLS_45 } = __VLS_43.slots;
        let __VLS_46;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_47 = __VLS_asFunctionalComponent1(__VLS_46, new __VLS_46({
            ...{ 'onClick': {} },
            type: "text",
            size: "small",
        }));
        const __VLS_48 = __VLS_47({
            ...{ 'onClick': {} },
            type: "text",
            size: "small",
        }, ...__VLS_functionalComponentArgsRest(__VLS_47));
        let __VLS_51;
        const __VLS_52 = ({ click: {} },
            { onClick: (__VLS_ctx.openCreateFolderModal) });
        const { default: __VLS_53 } = __VLS_49.slots;
        {
            const { icon: __VLS_54 } = __VLS_49.slots;
            let __VLS_55;
            /** @ts-ignore @type { | typeof __VLS_components.FolderAddOutlined} */
            FolderAddOutlined;
            // @ts-ignore
            const __VLS_56 = __VLS_asFunctionalComponent1(__VLS_55, new __VLS_55({}));
            const __VLS_57 = __VLS_56({}, ...__VLS_functionalComponentArgsRest(__VLS_56));
            // @ts-ignore
            [openCreateFolderModal,];
        }
        // @ts-ignore
        [];
        var __VLS_49;
        var __VLS_50;
        // @ts-ignore
        [];
        var __VLS_43;
        let __VLS_60;
        /** @ts-ignore @type { | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip'] | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip']} */
        aTooltip;
        // @ts-ignore
        const __VLS_61 = __VLS_asFunctionalComponent1(__VLS_60, new __VLS_60({
            title: "上传文件",
        }));
        const __VLS_62 = __VLS_61({
            title: "上传文件",
        }, ...__VLS_functionalComponentArgsRest(__VLS_61));
        const { default: __VLS_65 } = __VLS_63.slots;
        let __VLS_66;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_67 = __VLS_asFunctionalComponent1(__VLS_66, new __VLS_66({
            ...{ 'onClick': {} },
            type: "text",
            size: "small",
        }));
        const __VLS_68 = __VLS_67({
            ...{ 'onClick': {} },
            type: "text",
            size: "small",
        }, ...__VLS_functionalComponentArgsRest(__VLS_67));
        let __VLS_71;
        const __VLS_72 = ({ click: {} },
            { onClick: (__VLS_ctx.triggerFileUpload) });
        const { default: __VLS_73 } = __VLS_69.slots;
        {
            const { icon: __VLS_74 } = __VLS_69.slots;
            let __VLS_75;
            /** @ts-ignore @type { | typeof __VLS_components.UploadOutlined} */
            UploadOutlined;
            // @ts-ignore
            const __VLS_76 = __VLS_asFunctionalComponent1(__VLS_75, new __VLS_75({}));
            const __VLS_77 = __VLS_76({}, ...__VLS_functionalComponentArgsRest(__VLS_76));
            // @ts-ignore
            [triggerFileUpload,];
        }
        // @ts-ignore
        [];
        var __VLS_69;
        var __VLS_70;
        // @ts-ignore
        [];
        var __VLS_63;
        let __VLS_80;
        /** @ts-ignore @type { | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip'] | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip']} */
        aTooltip;
        // @ts-ignore
        const __VLS_81 = __VLS_asFunctionalComponent1(__VLS_80, new __VLS_80({
            title: "重命名",
        }));
        const __VLS_82 = __VLS_81({
            title: "重命名",
        }, ...__VLS_functionalComponentArgsRest(__VLS_81));
        const { default: __VLS_85 } = __VLS_83.slots;
        let __VLS_86;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_87 = __VLS_asFunctionalComponent1(__VLS_86, new __VLS_86({
            ...{ 'onClick': {} },
            type: "text",
            size: "small",
            disabled: (!__VLS_ctx.selectedFileNode),
        }));
        const __VLS_88 = __VLS_87({
            ...{ 'onClick': {} },
            type: "text",
            size: "small",
            disabled: (!__VLS_ctx.selectedFileNode),
        }, ...__VLS_functionalComponentArgsRest(__VLS_87));
        let __VLS_91;
        const __VLS_92 = ({ click: {} },
            { onClick: (__VLS_ctx.openRenameModal) });
        const { default: __VLS_93 } = __VLS_89.slots;
        {
            const { icon: __VLS_94 } = __VLS_89.slots;
            let __VLS_95;
            /** @ts-ignore @type { | typeof __VLS_components.EditOutlined} */
            EditOutlined;
            // @ts-ignore
            const __VLS_96 = __VLS_asFunctionalComponent1(__VLS_95, new __VLS_95({}));
            const __VLS_97 = __VLS_96({}, ...__VLS_functionalComponentArgsRest(__VLS_96));
            // @ts-ignore
            [selectedFileNode, openRenameModal,];
        }
        // @ts-ignore
        [];
        var __VLS_89;
        var __VLS_90;
        // @ts-ignore
        [];
        var __VLS_83;
        let __VLS_100;
        /** @ts-ignore @type { | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip'] | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip']} */
        aTooltip;
        // @ts-ignore
        const __VLS_101 = __VLS_asFunctionalComponent1(__VLS_100, new __VLS_100({
            title: "删除选中项",
        }));
        const __VLS_102 = __VLS_101({
            title: "删除选中项",
        }, ...__VLS_functionalComponentArgsRest(__VLS_101));
        const { default: __VLS_105 } = __VLS_103.slots;
        let __VLS_106;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_107 = __VLS_asFunctionalComponent1(__VLS_106, new __VLS_106({
            ...{ 'onClick': {} },
            type: "text",
            size: "small",
            disabled: (!__VLS_ctx.selectedFileNode),
        }));
        const __VLS_108 = __VLS_107({
            ...{ 'onClick': {} },
            type: "text",
            size: "small",
            disabled: (!__VLS_ctx.selectedFileNode),
        }, ...__VLS_functionalComponentArgsRest(__VLS_107));
        let __VLS_111;
        const __VLS_112 = ({ click: {} },
            { onClick: (__VLS_ctx.deleteSelectedNode) });
        const { default: __VLS_113 } = __VLS_109.slots;
        {
            const { icon: __VLS_114 } = __VLS_109.slots;
            let __VLS_115;
            /** @ts-ignore @type { | typeof __VLS_components.DeleteOutlined} */
            DeleteOutlined;
            // @ts-ignore
            const __VLS_116 = __VLS_asFunctionalComponent1(__VLS_115, new __VLS_115({}));
            const __VLS_117 = __VLS_116({}, ...__VLS_functionalComponentArgsRest(__VLS_116));
            // @ts-ignore
            [selectedFileNode, deleteSelectedNode,];
        }
        // @ts-ignore
        [];
        var __VLS_109;
        var __VLS_110;
        // @ts-ignore
        [];
        var __VLS_103;
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "side-panel-content" },
    });
    /** @type {__VLS_StyleScopedClasses['side-panel-content']} */ ;
    if (__VLS_ctx.sidePanelTab === 'data') {
        if (__VLS_ctx.loadingDbTables) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "sidebar-loading" },
            });
            /** @type {__VLS_StyleScopedClasses['sidebar-loading']} */ ;
            let __VLS_120;
            /** @ts-ignore @type { | typeof __VLS_components.aSpin | typeof __VLS_components.ASpin | typeof __VLS_components['a-spin']} */
            aSpin;
            // @ts-ignore
            const __VLS_121 = __VLS_asFunctionalComponent1(__VLS_120, new __VLS_120({
                size: "small",
            }));
            const __VLS_122 = __VLS_121({
                size: "small",
            }, ...__VLS_functionalComponentArgsRest(__VLS_121));
        }
        else if (!__VLS_ctx.dbTables.length) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "sidebar-empty" },
            });
            /** @type {__VLS_StyleScopedClasses['sidebar-empty']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
            (__VLS_ctx.appInfo?.dbName ? '暂无数据表' : '未配置项目库');
        }
        else {
            let __VLS_125;
            /** @ts-ignore @type { | typeof __VLS_components.aTree | typeof __VLS_components.ATree | typeof __VLS_components['a-tree']} */
            aTree;
            // @ts-ignore
            const __VLS_126 = __VLS_asFunctionalComponent1(__VLS_125, new __VLS_125({
                treeData: (__VLS_ctx.dbTableTree),
                defaultExpandAll: (true),
                ...{ class: "sidebar-tree" },
            }));
            const __VLS_127 = __VLS_126({
                treeData: (__VLS_ctx.dbTableTree),
                defaultExpandAll: (true),
                ...{ class: "sidebar-tree" },
            }, ...__VLS_functionalComponentArgsRest(__VLS_126));
            /** @type {__VLS_StyleScopedClasses['sidebar-tree']} */ ;
        }
    }
    else {
        if (__VLS_ctx.loadingSourceTree) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "sidebar-loading" },
            });
            /** @type {__VLS_StyleScopedClasses['sidebar-loading']} */ ;
            let __VLS_130;
            /** @ts-ignore @type { | typeof __VLS_components.aSpin | typeof __VLS_components.ASpin | typeof __VLS_components['a-spin']} */
            aSpin;
            // @ts-ignore
            const __VLS_131 = __VLS_asFunctionalComponent1(__VLS_130, new __VLS_130({
                size: "small",
            }));
            const __VLS_132 = __VLS_131({
                size: "small",
            }, ...__VLS_functionalComponentArgsRest(__VLS_131));
        }
        else if (!__VLS_ctx.sourceFileTree.length) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "sidebar-empty" },
            });
            /** @type {__VLS_StyleScopedClasses['sidebar-empty']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
        }
        else {
            let __VLS_135;
            /** @ts-ignore @type { | typeof __VLS_components.aTree | typeof __VLS_components.ATree | typeof __VLS_components['a-tree']} */
            aTree;
            // @ts-ignore
            const __VLS_136 = __VLS_asFunctionalComponent1(__VLS_135, new __VLS_135({
                ...{ 'onSelect': {} },
                ...{ 'onDblclick': {} },
                treeData: (__VLS_ctx.sourceFileTree),
                selectedKeys: (__VLS_ctx.selectedNodeKey ? [__VLS_ctx.selectedNodeKey] : []),
                defaultExpandAll: (true),
                ...{ class: "sidebar-tree" },
            }));
            const __VLS_137 = __VLS_136({
                ...{ 'onSelect': {} },
                ...{ 'onDblclick': {} },
                treeData: (__VLS_ctx.sourceFileTree),
                selectedKeys: (__VLS_ctx.selectedNodeKey ? [__VLS_ctx.selectedNodeKey] : []),
                defaultExpandAll: (true),
                ...{ class: "sidebar-tree" },
            }, ...__VLS_functionalComponentArgsRest(__VLS_136));
            let __VLS_140;
            const __VLS_141 = ({ select: {} },
                { onSelect: (__VLS_ctx.handleTreeNodeSelect) });
            const __VLS_142 = ({ dblclick: {} },
                { onDblclick: (__VLS_ctx.handleSourceFileDoubleClick) });
            /** @type {__VLS_StyleScopedClasses['sidebar-tree']} */ ;
            var __VLS_138;
            var __VLS_139;
        }
    }
}
if (__VLS_ctx.appId && __VLS_ctx.sidePanelVisible) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div)({
        ...{ onMousedown: (...[$event]) => {
                if (!(__VLS_ctx.appId && __VLS_ctx.sidePanelVisible))
                    return;
                __VLS_ctx.onResizeStart('sidebar', $event);
                // @ts-ignore
                [appId, sidePanelTab, sidePanelVisible, loadingDbTables, dbTables, appInfo, dbTableTree, loadingSourceTree, sourceFileTree, sourceFileTree, selectedNodeKey, selectedNodeKey, handleTreeNodeSelect, handleSourceFileDoubleClick, onResizeStart,];
            } },
        ...{ class: "resizer" },
    });
    /** @type {__VLS_StyleScopedClasses['resizer']} */ ;
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "center-column" },
});
/** @type {__VLS_StyleScopedClasses['center-column']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "center-work-area" },
});
/** @type {__VLS_StyleScopedClasses['center-work-area']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "file-tab-bar" },
});
/** @type {__VLS_StyleScopedClasses['file-tab-bar']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "file-tab-bar-left" },
});
/** @type {__VLS_StyleScopedClasses['file-tab-bar-left']} */ ;
for (const [tab] of __VLS_vFor((__VLS_ctx.fileTabs))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.activateFileTab(tab.path);
                // @ts-ignore
                [fileTabs, activateFileTab,];
            } },
        key: (tab.path),
        ...{ class: "file-tab" },
        ...{ class: ({ active: tab.path === __VLS_ctx.activeFileTab }) },
    });
    /** @type {__VLS_StyleScopedClasses['file-tab']} */ ;
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    if (tab.isDirty) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ class: "file-tab-dirty" },
            title: "未保存的更改",
        });
        /** @type {__VLS_StyleScopedClasses['file-tab-dirty']} */ ;
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "file-tab-name" },
    });
    /** @type {__VLS_StyleScopedClasses['file-tab-name']} */ ;
    (tab.name);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.closeFileTab(tab.path);
                // @ts-ignore
                [activeFileTab, closeFileTab,];
            } },
        ...{ class: "file-tab-close" },
    });
    /** @type {__VLS_StyleScopedClasses['file-tab-close']} */ ;
    let __VLS_143;
    /** @ts-ignore @type { | typeof __VLS_components.CloseOutlined} */
    CloseOutlined;
    // @ts-ignore
    const __VLS_144 = __VLS_asFunctionalComponent1(__VLS_143, new __VLS_143({}));
    const __VLS_145 = __VLS_144({}, ...__VLS_functionalComponentArgsRest(__VLS_144));
    // @ts-ignore
    [];
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "file-tab-bar-right" },
});
/** @type {__VLS_StyleScopedClasses['file-tab-bar-right']} */ ;
let __VLS_148;
/** @ts-ignore @type { | typeof __VLS_components.aSelect | typeof __VLS_components.ASelect | typeof __VLS_components['a-select']} */
aSelect;
// @ts-ignore
const __VLS_149 = __VLS_asFunctionalComponent1(__VLS_148, new __VLS_148({
    value: (__VLS_ctx.selectedPythonEnv),
    size: "small",
    ...{ style: {} },
    options: (__VLS_ctx.pythonEnvOptions),
    placeholder: "环境",
}));
const __VLS_150 = __VLS_149({
    value: (__VLS_ctx.selectedPythonEnv),
    size: "small",
    ...{ style: {} },
    options: (__VLS_ctx.pythonEnvOptions),
    placeholder: "环境",
}, ...__VLS_functionalComponentArgsRest(__VLS_149));
let __VLS_153;
/** @ts-ignore @type { | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip'] | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip']} */
aTooltip;
// @ts-ignore
const __VLS_154 = __VLS_asFunctionalComponent1(__VLS_153, new __VLS_153({
    title: "运行脚本",
}));
const __VLS_155 = __VLS_154({
    title: "运行脚本",
}, ...__VLS_functionalComponentArgsRest(__VLS_154));
const { default: __VLS_158 } = __VLS_156.slots;
let __VLS_159;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_160 = __VLS_asFunctionalComponent1(__VLS_159, new __VLS_159({
    ...{ 'onClick': {} },
    type: "text",
    size: "small",
    loading: (__VLS_ctx.runningScript),
    disabled: (!__VLS_ctx.activeTabData || !__VLS_ctx.activeTabData.name.endsWith('.py')),
}));
const __VLS_161 = __VLS_160({
    ...{ 'onClick': {} },
    type: "text",
    size: "small",
    loading: (__VLS_ctx.runningScript),
    disabled: (!__VLS_ctx.activeTabData || !__VLS_ctx.activeTabData.name.endsWith('.py')),
}, ...__VLS_functionalComponentArgsRest(__VLS_160));
let __VLS_164;
const __VLS_165 = ({ click: {} },
    { onClick: (__VLS_ctx.runScript) });
const { default: __VLS_166 } = __VLS_162.slots;
{
    const { icon: __VLS_167 } = __VLS_162.slots;
    let __VLS_168;
    /** @ts-ignore @type { | typeof __VLS_components.CaretRightOutlined} */
    CaretRightOutlined;
    // @ts-ignore
    const __VLS_169 = __VLS_asFunctionalComponent1(__VLS_168, new __VLS_168({}));
    const __VLS_170 = __VLS_169({}, ...__VLS_functionalComponentArgsRest(__VLS_169));
    // @ts-ignore
    [selectedPythonEnv, pythonEnvOptions, runningScript, activeTabData, activeTabData, runScript,];
}
// @ts-ignore
[];
var __VLS_162;
var __VLS_163;
// @ts-ignore
[];
var __VLS_156;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "editor-area" },
});
/** @type {__VLS_StyleScopedClasses['editor-area']} */ ;
if (!__VLS_ctx.fileTabs.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "center-empty" },
    });
    /** @type {__VLS_StyleScopedClasses['center-empty']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "center-empty-icon" },
    });
    /** @type {__VLS_StyleScopedClasses['center-empty-icon']} */ ;
    let __VLS_173;
    /** @ts-ignore @type { | typeof __VLS_components.FileTextOutlined} */
    FileTextOutlined;
    // @ts-ignore
    const __VLS_174 = __VLS_asFunctionalComponent1(__VLS_173, new __VLS_173({}));
    const __VLS_175 = __VLS_174({}, ...__VLS_functionalComponentArgsRest(__VLS_174));
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "center-empty-title" },
    });
    /** @type {__VLS_StyleScopedClasses['center-empty-title']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "center-empty-hint" },
    });
    /** @type {__VLS_StyleScopedClasses['center-empty-hint']} */ ;
}
else {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "editor-wrapper" },
    });
    /** @type {__VLS_StyleScopedClasses['editor-wrapper']} */ ;
    if (__VLS_ctx.showLoading) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "editor-loading-overlay" },
        });
        /** @type {__VLS_StyleScopedClasses['editor-loading-overlay']} */ ;
        let __VLS_178;
        /** @ts-ignore @type { | typeof __VLS_components.aSpin | typeof __VLS_components.ASpin | typeof __VLS_components['a-spin']} */
        aSpin;
        // @ts-ignore
        const __VLS_179 = __VLS_asFunctionalComponent1(__VLS_178, new __VLS_178({
            size: "default",
        }));
        const __VLS_180 = __VLS_179({
            size: "default",
        }, ...__VLS_functionalComponentArgsRest(__VLS_179));
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    }
    if (__VLS_ctx.activeTabData) {
        let __VLS_183;
        /** @ts-ignore @type { | typeof __VLS_components.VueMonacoEditor} */
        VueMonacoEditor;
        // @ts-ignore
        const __VLS_184 = __VLS_asFunctionalComponent1(__VLS_183, new __VLS_183({
            ...{ 'onMount': {} },
            key: (__VLS_ctx.activeFileTab),
            value: (__VLS_ctx.activeTabData.content),
            language: (__VLS_ctx.activeTabData.language),
            theme: (__VLS_ctx.editorTheme),
            options: (__VLS_ctx.editorOptions),
            ...{ class: "monaco-editor-container" },
        }));
        const __VLS_185 = __VLS_184({
            ...{ 'onMount': {} },
            key: (__VLS_ctx.activeFileTab),
            value: (__VLS_ctx.activeTabData.content),
            language: (__VLS_ctx.activeTabData.language),
            theme: (__VLS_ctx.editorTheme),
            options: (__VLS_ctx.editorOptions),
            ...{ class: "monaco-editor-container" },
        }, ...__VLS_functionalComponentArgsRest(__VLS_184));
        let __VLS_188;
        const __VLS_189 = ({ mount: {} },
            { onMount: (__VLS_ctx.handleEditorMount) });
        /** @type {__VLS_StyleScopedClasses['monaco-editor-container']} */ ;
        var __VLS_186;
        var __VLS_187;
    }
}
if (__VLS_ctx.fileTabs.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div)({
        ...{ onMousedown: (...[$event]) => {
                if (!(__VLS_ctx.fileTabs.length))
                    return;
                __VLS_ctx.onResizeStart('output', $event);
                // @ts-ignore
                [onResizeStart, fileTabs, fileTabs, activeFileTab, activeTabData, activeTabData, activeTabData, showLoading, editorTheme, editorOptions, handleEditorMount,];
            } },
        ...{ class: "resizer-horizontal" },
    });
    /** @type {__VLS_StyleScopedClasses['resizer-horizontal']} */ ;
}
if (__VLS_ctx.fileTabs.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "output-panel" },
        ...{ style: ({ height: __VLS_ctx.outputHeight + 'px' }) },
    });
    /** @type {__VLS_StyleScopedClasses['output-panel']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "output-header" },
    });
    /** @type {__VLS_StyleScopedClasses['output-header']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "output-title" },
    });
    /** @type {__VLS_StyleScopedClasses['output-title']} */ ;
    let __VLS_190;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_191 = __VLS_asFunctionalComponent1(__VLS_190, new __VLS_190({
        ...{ 'onClick': {} },
        type: "text",
        size: "small",
    }));
    const __VLS_192 = __VLS_191({
        ...{ 'onClick': {} },
        type: "text",
        size: "small",
    }, ...__VLS_functionalComponentArgsRest(__VLS_191));
    let __VLS_195;
    const __VLS_196 = ({ click: {} },
        { onClick: (__VLS_ctx.clearOutput) });
    const { default: __VLS_197 } = __VLS_193.slots;
    {
        const { icon: __VLS_198 } = __VLS_193.slots;
        let __VLS_199;
        /** @ts-ignore @type { | typeof __VLS_components.ClearOutlined} */
        ClearOutlined;
        // @ts-ignore
        const __VLS_200 = __VLS_asFunctionalComponent1(__VLS_199, new __VLS_199({}));
        const __VLS_201 = __VLS_200({}, ...__VLS_functionalComponentArgsRest(__VLS_200));
        // @ts-ignore
        [fileTabs, outputHeight, clearOutput,];
    }
    // @ts-ignore
    [];
    var __VLS_193;
    var __VLS_194;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "output-content" },
        ref: "outputContainer",
    });
    /** @type {__VLS_StyleScopedClasses['output-content']} */ ;
    if (!__VLS_ctx.outputLines.length) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "output-empty" },
        });
        /** @type {__VLS_StyleScopedClasses['output-empty']} */ ;
    }
    for (const [line, i] of __VLS_vFor((__VLS_ctx.outputLines))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            key: (i),
            ...{ class: "output-line" },
            ...{ class: ('output-' + line.type) },
        });
        /** @type {__VLS_StyleScopedClasses['output-line']} */ ;
        (line.text);
        // @ts-ignore
        [outputLines, outputLines,];
    }
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div)({
    ...{ onMousedown: (...[$event]) => {
            __VLS_ctx.onResizeStart('chat', $event);
            // @ts-ignore
            [onResizeStart,];
        } },
    ...{ class: "resizer" },
});
/** @type {__VLS_StyleScopedClasses['resizer']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "right-chat-panel" },
    ...{ style: ({ width: __VLS_ctx.chatWidth + 'px' }) },
});
/** @type {__VLS_StyleScopedClasses['right-chat-panel']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "chat-header" },
});
/** @type {__VLS_StyleScopedClasses['chat-header']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
    ...{ class: "chat-header-title" },
});
/** @type {__VLS_StyleScopedClasses['chat-header-title']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "chat-header-actions" },
});
/** @type {__VLS_StyleScopedClasses['chat-header-actions']} */ ;
let __VLS_204;
/** @ts-ignore @type { | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip'] | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip']} */
aTooltip;
// @ts-ignore
const __VLS_205 = __VLS_asFunctionalComponent1(__VLS_204, new __VLS_204({
    title: "新建会话",
}));
const __VLS_206 = __VLS_205({
    title: "新建会话",
}, ...__VLS_functionalComponentArgsRest(__VLS_205));
const { default: __VLS_209 } = __VLS_207.slots;
let __VLS_210;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_211 = __VLS_asFunctionalComponent1(__VLS_210, new __VLS_210({
    ...{ 'onClick': {} },
    type: "text",
    size: "small",
}));
const __VLS_212 = __VLS_211({
    ...{ 'onClick': {} },
    type: "text",
    size: "small",
}, ...__VLS_functionalComponentArgsRest(__VLS_211));
let __VLS_215;
const __VLS_216 = ({ click: {} },
    { onClick: (__VLS_ctx.createNewSession) });
const { default: __VLS_217 } = __VLS_213.slots;
{
    const { icon: __VLS_218 } = __VLS_213.slots;
    let __VLS_219;
    /** @ts-ignore @type { | typeof __VLS_components.PlusOutlined} */
    PlusOutlined;
    // @ts-ignore
    const __VLS_220 = __VLS_asFunctionalComponent1(__VLS_219, new __VLS_219({}));
    const __VLS_221 = __VLS_220({}, ...__VLS_functionalComponentArgsRest(__VLS_220));
    // @ts-ignore
    [chatWidth, createNewSession,];
}
// @ts-ignore
[];
var __VLS_213;
var __VLS_214;
// @ts-ignore
[];
var __VLS_207;
let __VLS_224;
/** @ts-ignore @type { | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip'] | typeof __VLS_components.aTooltip | typeof __VLS_components.ATooltip | typeof __VLS_components['a-tooltip']} */
aTooltip;
// @ts-ignore
const __VLS_225 = __VLS_asFunctionalComponent1(__VLS_224, new __VLS_224({
    title: "会话历史",
}));
const __VLS_226 = __VLS_225({
    title: "会话历史",
}, ...__VLS_functionalComponentArgsRest(__VLS_225));
const { default: __VLS_229 } = __VLS_227.slots;
let __VLS_230;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_231 = __VLS_asFunctionalComponent1(__VLS_230, new __VLS_230({
    ...{ 'onClick': {} },
    type: "text",
    size: "small",
}));
const __VLS_232 = __VLS_231({
    ...{ 'onClick': {} },
    type: "text",
    size: "small",
}, ...__VLS_functionalComponentArgsRest(__VLS_231));
let __VLS_235;
const __VLS_236 = ({ click: {} },
    { onClick: (__VLS_ctx.toggleSessionHistory) });
const { default: __VLS_237 } = __VLS_233.slots;
{
    const { icon: __VLS_238 } = __VLS_233.slots;
    let __VLS_239;
    /** @ts-ignore @type { | typeof __VLS_components.HistoryOutlined} */
    HistoryOutlined;
    // @ts-ignore
    const __VLS_240 = __VLS_asFunctionalComponent1(__VLS_239, new __VLS_239({}));
    const __VLS_241 = __VLS_240({}, ...__VLS_functionalComponentArgsRest(__VLS_240));
    // @ts-ignore
    [toggleSessionHistory,];
}
// @ts-ignore
[];
var __VLS_233;
var __VLS_234;
// @ts-ignore
[];
var __VLS_227;
if (__VLS_ctx.sessionHistoryVisible) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "session-dropdown" },
    });
    /** @type {__VLS_StyleScopedClasses['session-dropdown']} */ ;
    let __VLS_244;
    /** @ts-ignore @type { | typeof __VLS_components.aSpin | typeof __VLS_components.ASpin | typeof __VLS_components['a-spin'] | typeof __VLS_components.aSpin | typeof __VLS_components.ASpin | typeof __VLS_components['a-spin']} */
    aSpin;
    // @ts-ignore
    const __VLS_245 = __VLS_asFunctionalComponent1(__VLS_244, new __VLS_244({
        spinning: (__VLS_ctx.loadingSessionHistory),
    }));
    const __VLS_246 = __VLS_245({
        spinning: (__VLS_ctx.loadingSessionHistory),
    }, ...__VLS_functionalComponentArgsRest(__VLS_245));
    const { default: __VLS_249 } = __VLS_247.slots;
    if (!__VLS_ctx.sessionList.length && !__VLS_ctx.loadingSessionHistory) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "session-empty" },
        });
        /** @type {__VLS_StyleScopedClasses['session-empty']} */ ;
    }
    for (const [sess] of __VLS_vFor((__VLS_ctx.sessionList))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.sessionHistoryVisible))
                        return;
                    __VLS_ctx.loadSession(sess.session_id);
                    // @ts-ignore
                    [sessionHistoryVisible, loadingSessionHistory, loadingSessionHistory, sessionList, sessionList, loadSession,];
                } },
            key: (sess.session_id),
            ...{ class: "session-item" },
        });
        /** @type {__VLS_StyleScopedClasses['session-item']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "session-item-text" },
        });
        /** @type {__VLS_StyleScopedClasses['session-item-text']} */ ;
        (sess.first_message);
        // @ts-ignore
        [];
    }
    // @ts-ignore
    [];
    var __VLS_247;
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "messages-container" },
    ref: "messagesContainer",
});
/** @type {__VLS_StyleScopedClasses['messages-container']} */ ;
if (!__VLS_ctx.appId && !__VLS_ctx.messages.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "create-mode-hint" },
    });
    /** @type {__VLS_StyleScopedClasses['create-mode-hint']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "hint-icon" },
    });
    /** @type {__VLS_StyleScopedClasses['hint-icon']} */ ;
    let __VLS_250;
    /** @ts-ignore @type { | typeof __VLS_components.MessageOutlined} */
    MessageOutlined;
    // @ts-ignore
    const __VLS_251 = __VLS_asFunctionalComponent1(__VLS_250, new __VLS_250({}));
    const __VLS_252 = __VLS_251({}, ...__VLS_functionalComponentArgsRest(__VLS_251));
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "hint-title" },
    });
    /** @type {__VLS_StyleScopedClasses['hint-title']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "hint-description" },
    });
    /** @type {__VLS_StyleScopedClasses['hint-description']} */ ;
}
for (const [messageItem, index] of __VLS_vFor((__VLS_ctx.messages))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        key: (index),
        ...{ class: "message-item" },
    });
    /** @type {__VLS_StyleScopedClasses['message-item']} */ ;
    if (messageItem.type === 'user') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "user-message" },
        });
        /** @type {__VLS_StyleScopedClasses['user-message']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "message-bubble user-bubble" },
        });
        /** @type {__VLS_StyleScopedClasses['message-bubble']} */ ;
        /** @type {__VLS_StyleScopedClasses['user-bubble']} */ ;
        (messageItem.content);
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "message-avatar" },
        });
        /** @type {__VLS_StyleScopedClasses['message-avatar']} */ ;
        let __VLS_255;
        /** @ts-ignore @type { | typeof __VLS_components.aAvatar | typeof __VLS_components.AAvatar | typeof __VLS_components['a-avatar'] | typeof __VLS_components.aAvatar | typeof __VLS_components.AAvatar | typeof __VLS_components['a-avatar']} */
        aAvatar;
        // @ts-ignore
        const __VLS_256 = __VLS_asFunctionalComponent1(__VLS_255, new __VLS_255({
            src: (__VLS_ctx.loginUserStore.loginUser.userAvatar || undefined),
            size: (28),
        }));
        const __VLS_257 = __VLS_256({
            src: (__VLS_ctx.loginUserStore.loginUser.userAvatar || undefined),
            size: (28),
        }, ...__VLS_functionalComponentArgsRest(__VLS_256));
        const { default: __VLS_260 } = __VLS_258.slots;
        (__VLS_ctx.loginUserStore.loginUser.userName?.charAt(0) || 'U');
        // @ts-ignore
        [appId, messages, messages, loginUserStore, loginUserStore,];
        var __VLS_258;
    }
    else {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "ai-message" },
        });
        /** @type {__VLS_StyleScopedClasses['ai-message']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "message-avatar" },
        });
        /** @type {__VLS_StyleScopedClasses['message-avatar']} */ ;
        let __VLS_261;
        /** @ts-ignore @type { | typeof __VLS_components.aAvatar | typeof __VLS_components.AAvatar | typeof __VLS_components['a-avatar']} */
        aAvatar;
        // @ts-ignore
        const __VLS_262 = __VLS_asFunctionalComponent1(__VLS_261, new __VLS_261({
            src: (__VLS_ctx.aiAvatar),
            size: (28),
        }));
        const __VLS_263 = __VLS_262({
            src: (__VLS_ctx.aiAvatar),
            size: (28),
        }, ...__VLS_functionalComponentArgsRest(__VLS_262));
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "ai-content" },
        });
        /** @type {__VLS_StyleScopedClasses['ai-content']} */ ;
        if (messageItem.items?.length) {
            for (const [item, ii] of __VLS_vFor((messageItem.items))) {
                __VLS_asFunctionalElement(__VLS_intrinsics.template)({
                    key: (ii),
                });
                if (item.type === 'text') {
                    const __VLS_266 = MarkdownRenderer;
                    // @ts-ignore
                    const __VLS_267 = __VLS_asFunctionalComponent1(__VLS_266, new __VLS_266({
                        content: (item.text || ''),
                    }));
                    const __VLS_268 = __VLS_267({
                        content: (item.text || ''),
                    }, ...__VLS_functionalComponentArgsRest(__VLS_267));
                }
                else if (item.type === 'step' && item.step) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                        ...{ class: "ai-step-inline" },
                        ...{ class: ('step-' + item.step.state) },
                    });
                    /** @type {__VLS_StyleScopedClasses['ai-step-inline']} */ ;
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        ...{ class: "step-icon" },
                    });
                    /** @type {__VLS_StyleScopedClasses['step-icon']} */ ;
                    if (item.step.state === 'running') {
                        let __VLS_271;
                        /** @ts-ignore @type { | typeof __VLS_components.LoadingOutlined} */
                        LoadingOutlined;
                        // @ts-ignore
                        const __VLS_272 = __VLS_asFunctionalComponent1(__VLS_271, new __VLS_271({
                            spin: true,
                        }));
                        const __VLS_273 = __VLS_272({
                            spin: true,
                        }, ...__VLS_functionalComponentArgsRest(__VLS_272));
                    }
                    else if (item.step.state === 'completed') {
                        let __VLS_276;
                        /** @ts-ignore @type { | typeof __VLS_components.CheckCircleOutlined} */
                        CheckCircleOutlined;
                        // @ts-ignore
                        const __VLS_277 = __VLS_asFunctionalComponent1(__VLS_276, new __VLS_276({}));
                        const __VLS_278 = __VLS_277({}, ...__VLS_functionalComponentArgsRest(__VLS_277));
                    }
                    else {
                        let __VLS_281;
                        /** @ts-ignore @type { | typeof __VLS_components.CloseCircleOutlined} */
                        CloseCircleOutlined;
                        // @ts-ignore
                        const __VLS_282 = __VLS_asFunctionalComponent1(__VLS_281, new __VLS_281({}));
                        const __VLS_283 = __VLS_282({}, ...__VLS_functionalComponentArgsRest(__VLS_282));
                    }
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        ...{ class: "step-desc" },
                    });
                    /** @type {__VLS_StyleScopedClasses['step-desc']} */ ;
                    (item.step.description);
                    if (item.step.detail) {
                        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                            ...{ class: "step-detail" },
                        });
                        /** @type {__VLS_StyleScopedClasses['step-detail']} */ ;
                        (item.step.detail);
                    }
                }
                else if (item.type === 'tool' && item.toolResult) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                        ...{ class: "tool-result-inline" },
                    });
                    /** @type {__VLS_StyleScopedClasses['tool-result-inline']} */ ;
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        ...{ class: "tool-icon" },
                    });
                    /** @type {__VLS_StyleScopedClasses['tool-icon']} */ ;
                    let __VLS_286;
                    /** @ts-ignore @type { | typeof __VLS_components.CodeOutlined} */
                    CodeOutlined;
                    // @ts-ignore
                    const __VLS_287 = __VLS_asFunctionalComponent1(__VLS_286, new __VLS_286({}));
                    const __VLS_288 = __VLS_287({}, ...__VLS_functionalComponentArgsRest(__VLS_287));
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        ...{ class: "tool-name" },
                    });
                    /** @type {__VLS_StyleScopedClasses['tool-name']} */ ;
                    (item.toolResult.name);
                    if (item.toolResult.detail) {
                        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                            ...{ class: "tool-detail" },
                        });
                        /** @type {__VLS_StyleScopedClasses['tool-detail']} */ ;
                        (item.toolResult.detail);
                    }
                }
                // @ts-ignore
                [aiAvatar,];
            }
        }
        else {
            if (messageItem.content) {
                const __VLS_291 = MarkdownRenderer;
                // @ts-ignore
                const __VLS_292 = __VLS_asFunctionalComponent1(__VLS_291, new __VLS_291({
                    content: (messageItem.content),
                }));
                const __VLS_293 = __VLS_292({
                    content: (messageItem.content),
                }, ...__VLS_functionalComponentArgsRest(__VLS_292));
            }
            if (messageItem.steps?.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "ai-steps" },
                });
                /** @type {__VLS_StyleScopedClasses['ai-steps']} */ ;
                for (const [step, si] of __VLS_vFor((messageItem.steps))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                        key: (si),
                        ...{ class: "ai-step" },
                        ...{ class: ('step-' + step.state) },
                    });
                    /** @type {__VLS_StyleScopedClasses['ai-step']} */ ;
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        ...{ class: "step-icon" },
                    });
                    /** @type {__VLS_StyleScopedClasses['step-icon']} */ ;
                    if (step.state === 'running') {
                        let __VLS_296;
                        /** @ts-ignore @type { | typeof __VLS_components.LoadingOutlined} */
                        LoadingOutlined;
                        // @ts-ignore
                        const __VLS_297 = __VLS_asFunctionalComponent1(__VLS_296, new __VLS_296({
                            spin: true,
                        }));
                        const __VLS_298 = __VLS_297({
                            spin: true,
                        }, ...__VLS_functionalComponentArgsRest(__VLS_297));
                    }
                    else if (step.state === 'completed') {
                        let __VLS_301;
                        /** @ts-ignore @type { | typeof __VLS_components.CheckCircleOutlined} */
                        CheckCircleOutlined;
                        // @ts-ignore
                        const __VLS_302 = __VLS_asFunctionalComponent1(__VLS_301, new __VLS_301({}));
                        const __VLS_303 = __VLS_302({}, ...__VLS_functionalComponentArgsRest(__VLS_302));
                    }
                    else {
                        let __VLS_306;
                        /** @ts-ignore @type { | typeof __VLS_components.CloseCircleOutlined} */
                        CloseCircleOutlined;
                        // @ts-ignore
                        const __VLS_307 = __VLS_asFunctionalComponent1(__VLS_306, new __VLS_306({}));
                        const __VLS_308 = __VLS_307({}, ...__VLS_functionalComponentArgsRest(__VLS_307));
                    }
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        ...{ class: "step-desc" },
                    });
                    /** @type {__VLS_StyleScopedClasses['step-desc']} */ ;
                    (step.description);
                    if (step.detail) {
                        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                            ...{ class: "step-detail" },
                        });
                        /** @type {__VLS_StyleScopedClasses['step-detail']} */ ;
                        (step.detail);
                    }
                    // @ts-ignore
                    [];
                }
            }
        }
        if (messageItem.loading) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "loading-indicator" },
            });
            /** @type {__VLS_StyleScopedClasses['loading-indicator']} */ ;
            let __VLS_311;
            /** @ts-ignore @type { | typeof __VLS_components.aSpin | typeof __VLS_components.ASpin | typeof __VLS_components['a-spin']} */
            aSpin;
            // @ts-ignore
            const __VLS_312 = __VLS_asFunctionalComponent1(__VLS_311, new __VLS_311({
                size: "small",
            }));
            const __VLS_313 = __VLS_312({
                size: "small",
            }, ...__VLS_functionalComponentArgsRest(__VLS_312));
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        }
    }
    // @ts-ignore
    [];
}
if (__VLS_ctx.selectedElementInfo) {
    let __VLS_316;
    /** @ts-ignore @type { | typeof __VLS_components.aAlert | typeof __VLS_components.AAlert | typeof __VLS_components['a-alert'] | typeof __VLS_components.aAlert | typeof __VLS_components.AAlert | typeof __VLS_components['a-alert']} */
    aAlert;
    // @ts-ignore
    const __VLS_317 = __VLS_asFunctionalComponent1(__VLS_316, new __VLS_316({
        ...{ 'onClose': {} },
        ...{ class: "selected-element-alert" },
        type: "info",
        closable: true,
    }));
    const __VLS_318 = __VLS_317({
        ...{ 'onClose': {} },
        ...{ class: "selected-element-alert" },
        type: "info",
        closable: true,
    }, ...__VLS_functionalComponentArgsRest(__VLS_317));
    let __VLS_321;
    const __VLS_322 = ({ close: {} },
        { onClose: (__VLS_ctx.clearSelectedElement) });
    /** @type {__VLS_StyleScopedClasses['selected-element-alert']} */ ;
    const { default: __VLS_323 } = __VLS_319.slots;
    {
        const { message: __VLS_324 } = __VLS_319.slots;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "selected-element-info" },
        });
        /** @type {__VLS_StyleScopedClasses['selected-element-info']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "element-header" },
        });
        /** @type {__VLS_StyleScopedClasses['element-header']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ class: "element-tag" },
        });
        /** @type {__VLS_StyleScopedClasses['element-tag']} */ ;
        (__VLS_ctx.selectedElementInfo.tagName.toLowerCase());
        if (__VLS_ctx.selectedElementInfo.id) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "element-id" },
            });
            /** @type {__VLS_StyleScopedClasses['element-id']} */ ;
            (__VLS_ctx.selectedElementInfo.id);
        }
        if (__VLS_ctx.selectedElementInfo.className) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "element-class" },
            });
            /** @type {__VLS_StyleScopedClasses['element-class']} */ ;
            (__VLS_ctx.selectedElementInfo.className.split(' ').join('.'));
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "element-details" },
        });
        /** @type {__VLS_StyleScopedClasses['element-details']} */ ;
        if (__VLS_ctx.selectedElementInfo.textContent) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "element-item" },
            });
            /** @type {__VLS_StyleScopedClasses['element-item']} */ ;
            (__VLS_ctx.selectedElementInfo.textContent.substring(0, 50));
            (__VLS_ctx.selectedElementInfo.textContent.length > 50 ? '...' : '');
        }
        if (__VLS_ctx.selectedElementInfo.pagePath) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "element-item" },
            });
            /** @type {__VLS_StyleScopedClasses['element-item']} */ ;
            (__VLS_ctx.selectedElementInfo.pagePath);
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "element-item" },
        });
        /** @type {__VLS_StyleScopedClasses['element-item']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.code, __VLS_intrinsics.code)({
            ...{ class: "element-selector-code" },
        });
        /** @type {__VLS_StyleScopedClasses['element-selector-code']} */ ;
        (__VLS_ctx.selectedElementInfo.selector);
        // @ts-ignore
        [selectedElementInfo, selectedElementInfo, selectedElementInfo, selectedElementInfo, selectedElementInfo, selectedElementInfo, selectedElementInfo, selectedElementInfo, selectedElementInfo, selectedElementInfo, selectedElementInfo, selectedElementInfo, clearSelectedElement,];
    }
    // @ts-ignore
    [];
    var __VLS_319;
    var __VLS_320;
}
if (__VLS_ctx.appId && __VLS_ctx.tasks.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "task-board" },
        ...{ class: ({ collapsed: __VLS_ctx.taskBoardCollapsed }) },
    });
    /** @type {__VLS_StyleScopedClasses['task-board']} */ ;
    /** @type {__VLS_StyleScopedClasses['collapsed']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.appId && __VLS_ctx.tasks.length))
                    return;
                __VLS_ctx.taskBoardCollapsed = !__VLS_ctx.taskBoardCollapsed;
                // @ts-ignore
                [appId, tasks, taskBoardCollapsed, taskBoardCollapsed, taskBoardCollapsed,];
            } },
        ...{ class: "task-board-header" },
    });
    /** @type {__VLS_StyleScopedClasses['task-board-header']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "task-board-title" },
    });
    /** @type {__VLS_StyleScopedClasses['task-board-title']} */ ;
    let __VLS_325;
    /** @ts-ignore @type { | typeof __VLS_components.CheckSquareOutlined} */
    CheckSquareOutlined;
    // @ts-ignore
    const __VLS_326 = __VLS_asFunctionalComponent1(__VLS_325, new __VLS_325({}));
    const __VLS_327 = __VLS_326({}, ...__VLS_functionalComponentArgsRest(__VLS_326));
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (__VLS_ctx.visibleTasks.length ? '(' + __VLS_ctx.visibleTasks.filter(t => t.status === 'completed').length + '/' + __VLS_ctx.visibleTasks.length + ')' : '');
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "task-board-toggle" },
    });
    /** @type {__VLS_StyleScopedClasses['task-board-toggle']} */ ;
    if (!__VLS_ctx.taskBoardCollapsed) {
        let __VLS_330;
        /** @ts-ignore @type { | typeof __VLS_components.UpOutlined} */
        UpOutlined;
        // @ts-ignore
        const __VLS_331 = __VLS_asFunctionalComponent1(__VLS_330, new __VLS_330({}));
        const __VLS_332 = __VLS_331({}, ...__VLS_functionalComponentArgsRest(__VLS_331));
    }
    else {
        let __VLS_335;
        /** @ts-ignore @type { | typeof __VLS_components.DownOutlined} */
        DownOutlined;
        // @ts-ignore
        const __VLS_336 = __VLS_asFunctionalComponent1(__VLS_335, new __VLS_335({}));
        const __VLS_337 = __VLS_336({}, ...__VLS_functionalComponentArgsRest(__VLS_336));
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "task-board-body" },
    });
    __VLS_asFunctionalDirective(__VLS_directives.vShow, {})(null, { ...__VLS_directiveBindingRestFields, value: (!__VLS_ctx.taskBoardCollapsed) }, null, null);
    /** @type {__VLS_StyleScopedClasses['task-board-body']} */ ;
    if (!__VLS_ctx.visibleTasks.length) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "task-board-empty" },
        });
        /** @type {__VLS_StyleScopedClasses['task-board-empty']} */ ;
    }
    for (const [task] of __VLS_vFor((__VLS_ctx.visibleTasks))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            key: (task.id),
            ...{ class: "task-chip" },
            ...{ class: ('task-' + task.status) },
        });
        /** @type {__VLS_StyleScopedClasses['task-chip']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ class: "task-chip-indicator" },
        });
        /** @type {__VLS_StyleScopedClasses['task-chip-indicator']} */ ;
        if (task.status === 'completed') {
            let __VLS_340;
            /** @ts-ignore @type { | typeof __VLS_components.CheckCircleOutlined} */
            CheckCircleOutlined;
            // @ts-ignore
            const __VLS_341 = __VLS_asFunctionalComponent1(__VLS_340, new __VLS_340({}));
            const __VLS_342 = __VLS_341({}, ...__VLS_functionalComponentArgsRest(__VLS_341));
        }
        else if (task.status === 'in_progress') {
            let __VLS_345;
            /** @ts-ignore @type { | typeof __VLS_components.LoadingOutlined} */
            LoadingOutlined;
            // @ts-ignore
            const __VLS_346 = __VLS_asFunctionalComponent1(__VLS_345, new __VLS_345({}));
            const __VLS_347 = __VLS_346({}, ...__VLS_functionalComponentArgsRest(__VLS_346));
        }
        else {
            let __VLS_350;
            /** @ts-ignore @type { | typeof __VLS_components.ClockCircleOutlined} */
            ClockCircleOutlined;
            // @ts-ignore
            const __VLS_351 = __VLS_asFunctionalComponent1(__VLS_350, new __VLS_350({}));
            const __VLS_352 = __VLS_351({}, ...__VLS_functionalComponentArgsRest(__VLS_351));
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ class: "task-chip-subject" },
        });
        /** @type {__VLS_StyleScopedClasses['task-chip-subject']} */ ;
        (task.subject);
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ class: "task-chip-id" },
        });
        /** @type {__VLS_StyleScopedClasses['task-chip-id']} */ ;
        (task.id);
        if (task.blockedBy.length) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "task-chip-blocked" },
            });
            /** @type {__VLS_StyleScopedClasses['task-chip-blocked']} */ ;
            (task.blockedBy.join(','));
        }
        // @ts-ignore
        [taskBoardCollapsed, taskBoardCollapsed, visibleTasks, visibleTasks, visibleTasks, visibleTasks, visibleTasks,];
    }
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "input-container" },
    ...{ style: ({ height: __VLS_ctx.inputHeight + 'px' }) },
});
/** @type {__VLS_StyleScopedClasses['input-container']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div)({
    ...{ onMousedown: (...[$event]) => {
            __VLS_ctx.onResizeStart('input', $event);
            // @ts-ignore
            [onResizeStart, inputHeight,];
        } },
    ...{ class: "resizer-horizontal input-resizer" },
});
/** @type {__VLS_StyleScopedClasses['resizer-horizontal']} */ ;
/** @type {__VLS_StyleScopedClasses['input-resizer']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "input-wrapper" },
});
/** @type {__VLS_StyleScopedClasses['input-wrapper']} */ ;
let __VLS_355;
/** @ts-ignore @type { | typeof __VLS_components.aTextarea | typeof __VLS_components.ATextarea | typeof __VLS_components['a-textarea']} */
aTextarea;
// @ts-ignore
const __VLS_356 = __VLS_asFunctionalComponent1(__VLS_355, new __VLS_355({
    ...{ 'onKeydown': {} },
    value: (__VLS_ctx.userInput),
    placeholder: (__VLS_ctx.getInputPlaceholder()),
    maxlength: (1000),
    disabled: (__VLS_ctx.isGenerating || __VLS_ctx.isCreatingApp || !__VLS_ctx.canOperateApp),
    ...{ class: "chat-input" },
}));
const __VLS_357 = __VLS_356({
    ...{ 'onKeydown': {} },
    value: (__VLS_ctx.userInput),
    placeholder: (__VLS_ctx.getInputPlaceholder()),
    maxlength: (1000),
    disabled: (__VLS_ctx.isGenerating || __VLS_ctx.isCreatingApp || !__VLS_ctx.canOperateApp),
    ...{ class: "chat-input" },
}, ...__VLS_functionalComponentArgsRest(__VLS_356));
let __VLS_360;
const __VLS_361 = ({ keydown: {} },
    { onKeydown: (__VLS_ctx.sendMessage) });
/** @type {__VLS_StyleScopedClasses['chat-input']} */ ;
var __VLS_358;
var __VLS_359;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "input-actions" },
});
/** @type {__VLS_StyleScopedClasses['input-actions']} */ ;
if (__VLS_ctx.isGenerating) {
    let __VLS_362;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_363 = __VLS_asFunctionalComponent1(__VLS_362, new __VLS_362({
        ...{ 'onClick': {} },
        danger: true,
        loading: (__VLS_ctx.isStoppingGeneration),
        size: "small",
    }));
    const __VLS_364 = __VLS_363({
        ...{ 'onClick': {} },
        danger: true,
        loading: (__VLS_ctx.isStoppingGeneration),
        size: "small",
    }, ...__VLS_functionalComponentArgsRest(__VLS_363));
    let __VLS_367;
    const __VLS_368 = ({ click: {} },
        { onClick: (__VLS_ctx.stopGeneration) });
    const { default: __VLS_369 } = __VLS_365.slots;
    {
        const { icon: __VLS_370 } = __VLS_365.slots;
        let __VLS_371;
        /** @ts-ignore @type { | typeof __VLS_components.StopOutlined} */
        StopOutlined;
        // @ts-ignore
        const __VLS_372 = __VLS_asFunctionalComponent1(__VLS_371, new __VLS_371({}));
        const __VLS_373 = __VLS_372({}, ...__VLS_functionalComponentArgsRest(__VLS_372));
        // @ts-ignore
        [canOperateApp, userInput, getInputPlaceholder, isGenerating, isGenerating, isCreatingApp, sendMessage, isStoppingGeneration, stopGeneration,];
    }
    // @ts-ignore
    [];
    var __VLS_365;
    var __VLS_366;
}
else {
    let __VLS_376;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_377 = __VLS_asFunctionalComponent1(__VLS_376, new __VLS_376({
        ...{ 'onClick': {} },
        type: "primary",
        loading: (__VLS_ctx.isCreatingApp),
        disabled: (!__VLS_ctx.canOperateApp),
        size: "small",
    }));
    const __VLS_378 = __VLS_377({
        ...{ 'onClick': {} },
        type: "primary",
        loading: (__VLS_ctx.isCreatingApp),
        disabled: (!__VLS_ctx.canOperateApp),
        size: "small",
    }, ...__VLS_functionalComponentArgsRest(__VLS_377));
    let __VLS_381;
    const __VLS_382 = ({ click: {} },
        { onClick: (__VLS_ctx.sendMessage) });
    const { default: __VLS_383 } = __VLS_379.slots;
    {
        const { icon: __VLS_384 } = __VLS_379.slots;
        let __VLS_385;
        /** @ts-ignore @type { | typeof __VLS_components.SendOutlined} */
        SendOutlined;
        // @ts-ignore
        const __VLS_386 = __VLS_asFunctionalComponent1(__VLS_385, new __VLS_385({}));
        const __VLS_387 = __VLS_386({}, ...__VLS_functionalComponentArgsRest(__VLS_386));
        // @ts-ignore
        [canOperateApp, isCreatingApp, sendMessage,];
    }
    // @ts-ignore
    [];
    var __VLS_379;
    var __VLS_380;
}
let __VLS_390;
/** @ts-ignore @type { | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal'] | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal']} */
aModal;
// @ts-ignore
const __VLS_391 = __VLS_asFunctionalComponent1(__VLS_390, new __VLS_390({
    open: (__VLS_ctx.createFileModalVisible),
    title: "新建文件",
    maskClosable: (false),
}));
const __VLS_392 = __VLS_391({
    open: (__VLS_ctx.createFileModalVisible),
    title: "新建文件",
    maskClosable: (false),
}, ...__VLS_functionalComponentArgsRest(__VLS_391));
const { default: __VLS_395 } = __VLS_393.slots;
let __VLS_396;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_397 = __VLS_asFunctionalComponent1(__VLS_396, new __VLS_396({
    ...{ 'onKeydown': {} },
    value: (__VLS_ctx.newItemName),
    placeholder: "输入文件名，如 index.html",
}));
const __VLS_398 = __VLS_397({
    ...{ 'onKeydown': {} },
    value: (__VLS_ctx.newItemName),
    placeholder: "输入文件名，如 index.html",
}, ...__VLS_functionalComponentArgsRest(__VLS_397));
let __VLS_401;
const __VLS_402 = ({ keydown: {} },
    { onKeydown: (__VLS_ctx.doCreateFile) });
var __VLS_399;
var __VLS_400;
if (__VLS_ctx.newItemParentPath) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ style: {} },
    });
    (__VLS_ctx.newItemParentPath);
}
{
    const { footer: __VLS_403 } = __VLS_393.slots;
    let __VLS_404;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_405 = __VLS_asFunctionalComponent1(__VLS_404, new __VLS_404({
        ...{ 'onClick': {} },
    }));
    const __VLS_406 = __VLS_405({
        ...{ 'onClick': {} },
    }, ...__VLS_functionalComponentArgsRest(__VLS_405));
    let __VLS_409;
    const __VLS_410 = ({ click: {} },
        { onClick: (...[$event]) => {
                __VLS_ctx.createFileModalVisible = false;
                // @ts-ignore
                [createFileModalVisible, createFileModalVisible, newItemName, doCreateFile, newItemParentPath, newItemParentPath,];
            } });
    const { default: __VLS_411 } = __VLS_407.slots;
    // @ts-ignore
    [];
    var __VLS_407;
    var __VLS_408;
    let __VLS_412;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_413 = __VLS_asFunctionalComponent1(__VLS_412, new __VLS_412({
        ...{ 'onClick': {} },
        type: "primary",
        loading: (__VLS_ctx.fileOpLoading),
    }));
    const __VLS_414 = __VLS_413({
        ...{ 'onClick': {} },
        type: "primary",
        loading: (__VLS_ctx.fileOpLoading),
    }, ...__VLS_functionalComponentArgsRest(__VLS_413));
    let __VLS_417;
    const __VLS_418 = ({ click: {} },
        { onClick: (__VLS_ctx.doCreateFile) });
    const { default: __VLS_419 } = __VLS_415.slots;
    // @ts-ignore
    [doCreateFile, fileOpLoading,];
    var __VLS_415;
    var __VLS_416;
    // @ts-ignore
    [];
}
// @ts-ignore
[];
var __VLS_393;
let __VLS_420;
/** @ts-ignore @type { | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal'] | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal']} */
aModal;
// @ts-ignore
const __VLS_421 = __VLS_asFunctionalComponent1(__VLS_420, new __VLS_420({
    open: (__VLS_ctx.createFolderModalVisible),
    title: "新建文件夹",
    maskClosable: (false),
}));
const __VLS_422 = __VLS_421({
    open: (__VLS_ctx.createFolderModalVisible),
    title: "新建文件夹",
    maskClosable: (false),
}, ...__VLS_functionalComponentArgsRest(__VLS_421));
const { default: __VLS_425 } = __VLS_423.slots;
let __VLS_426;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_427 = __VLS_asFunctionalComponent1(__VLS_426, new __VLS_426({
    ...{ 'onKeydown': {} },
    value: (__VLS_ctx.newItemName),
    placeholder: "输入文件夹名，如 components",
}));
const __VLS_428 = __VLS_427({
    ...{ 'onKeydown': {} },
    value: (__VLS_ctx.newItemName),
    placeholder: "输入文件夹名，如 components",
}, ...__VLS_functionalComponentArgsRest(__VLS_427));
let __VLS_431;
const __VLS_432 = ({ keydown: {} },
    { onKeydown: (__VLS_ctx.doCreateFolder) });
var __VLS_429;
var __VLS_430;
if (__VLS_ctx.newItemParentPath) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ style: {} },
    });
    (__VLS_ctx.newItemParentPath);
}
{
    const { footer: __VLS_433 } = __VLS_423.slots;
    let __VLS_434;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_435 = __VLS_asFunctionalComponent1(__VLS_434, new __VLS_434({
        ...{ 'onClick': {} },
    }));
    const __VLS_436 = __VLS_435({
        ...{ 'onClick': {} },
    }, ...__VLS_functionalComponentArgsRest(__VLS_435));
    let __VLS_439;
    const __VLS_440 = ({ click: {} },
        { onClick: (...[$event]) => {
                __VLS_ctx.createFolderModalVisible = false;
                // @ts-ignore
                [newItemName, newItemParentPath, newItemParentPath, createFolderModalVisible, createFolderModalVisible, doCreateFolder,];
            } });
    const { default: __VLS_441 } = __VLS_437.slots;
    // @ts-ignore
    [];
    var __VLS_437;
    var __VLS_438;
    let __VLS_442;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_443 = __VLS_asFunctionalComponent1(__VLS_442, new __VLS_442({
        ...{ 'onClick': {} },
        type: "primary",
        loading: (__VLS_ctx.fileOpLoading),
    }));
    const __VLS_444 = __VLS_443({
        ...{ 'onClick': {} },
        type: "primary",
        loading: (__VLS_ctx.fileOpLoading),
    }, ...__VLS_functionalComponentArgsRest(__VLS_443));
    let __VLS_447;
    const __VLS_448 = ({ click: {} },
        { onClick: (__VLS_ctx.doCreateFolder) });
    const { default: __VLS_449 } = __VLS_445.slots;
    // @ts-ignore
    [fileOpLoading, doCreateFolder,];
    var __VLS_445;
    var __VLS_446;
    // @ts-ignore
    [];
}
// @ts-ignore
[];
var __VLS_423;
let __VLS_450;
/** @ts-ignore @type { | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal'] | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal']} */
aModal;
// @ts-ignore
const __VLS_451 = __VLS_asFunctionalComponent1(__VLS_450, new __VLS_450({
    open: (__VLS_ctx.renameModalVisible),
    title: "重命名",
    maskClosable: (false),
}));
const __VLS_452 = __VLS_451({
    open: (__VLS_ctx.renameModalVisible),
    title: "重命名",
    maskClosable: (false),
}, ...__VLS_functionalComponentArgsRest(__VLS_451));
const { default: __VLS_455 } = __VLS_453.slots;
let __VLS_456;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_457 = __VLS_asFunctionalComponent1(__VLS_456, new __VLS_456({
    ...{ 'onKeydown': {} },
    value: (__VLS_ctx.renameNewName),
    placeholder: "输入新名称",
}));
const __VLS_458 = __VLS_457({
    ...{ 'onKeydown': {} },
    value: (__VLS_ctx.renameNewName),
    placeholder: "输入新名称",
}, ...__VLS_functionalComponentArgsRest(__VLS_457));
let __VLS_461;
const __VLS_462 = ({ keydown: {} },
    { onKeydown: (__VLS_ctx.doRename) });
var __VLS_459;
var __VLS_460;
{
    const { footer: __VLS_463 } = __VLS_453.slots;
    let __VLS_464;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_465 = __VLS_asFunctionalComponent1(__VLS_464, new __VLS_464({
        ...{ 'onClick': {} },
    }));
    const __VLS_466 = __VLS_465({
        ...{ 'onClick': {} },
    }, ...__VLS_functionalComponentArgsRest(__VLS_465));
    let __VLS_469;
    const __VLS_470 = ({ click: {} },
        { onClick: (...[$event]) => {
                __VLS_ctx.renameModalVisible = false;
                // @ts-ignore
                [renameModalVisible, renameModalVisible, renameNewName, doRename,];
            } });
    const { default: __VLS_471 } = __VLS_467.slots;
    // @ts-ignore
    [];
    var __VLS_467;
    var __VLS_468;
    let __VLS_472;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_473 = __VLS_asFunctionalComponent1(__VLS_472, new __VLS_472({
        ...{ 'onClick': {} },
        type: "primary",
        loading: (__VLS_ctx.fileOpLoading),
    }));
    const __VLS_474 = __VLS_473({
        ...{ 'onClick': {} },
        type: "primary",
        loading: (__VLS_ctx.fileOpLoading),
    }, ...__VLS_functionalComponentArgsRest(__VLS_473));
    let __VLS_477;
    const __VLS_478 = ({ click: {} },
        { onClick: (__VLS_ctx.doRename) });
    const { default: __VLS_479 } = __VLS_475.slots;
    // @ts-ignore
    [fileOpLoading, doRename,];
    var __VLS_475;
    var __VLS_476;
    // @ts-ignore
    [];
}
// @ts-ignore
[];
var __VLS_453;
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
