import { createApp } from 'vue'
import App from './App.vue'

// 自托管字体（局域网离线可用）
import '@fontsource/cormorant-garamond/500.css'
import '@fontsource/cormorant-garamond/600.css'
import '@fontsource/cormorant-garamond/700.css'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'

// abcjs 播放控件样式
import 'abcjs/abcjs-audio.css'

// 设计系统
import './styles/design-system.css'

createApp(App).mount('#app')
