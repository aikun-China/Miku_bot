/**
 * MikuBot Admin - YAML 编辑器增强
 * 目前为基础版本，未来可扩展为完整的 YAML 语法高亮
 */

(function() {
    // Tab 键支持
    document.addEventListener('DOMContentLoaded', () => {
        const editor = document.getElementById('yaml-editor');
        if (!editor) return;

        editor.addEventListener('keydown', (e) => {
            if (e.key === 'Tab') {
                e.preventDefault();
                const start = editor.selectionStart;
                const end = editor.selectionEnd;
                editor.value = editor.value.substring(0, start) + '  ' + editor.value.substring(end);
                editor.selectionStart = editor.selectionEnd = start + 2;
            }
        });
    });
})();