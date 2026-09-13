import '@unocss/reset/tailwind.css'
import 'virtual:uno.css'
import './app.css'

import { mount } from 'svelte'
import App from './App.svelte'

const target = document.getElementById('app')
if (!target) throw new Error('Renderer mount point #app is missing from index.html')

export default mount(App, { target })
