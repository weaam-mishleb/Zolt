import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import AdminPage from './pages/AdminPage.jsx'
import './index.css'

// Minimal routing: the hidden /admin route renders the admin panel.
const path = window.location.pathname.replace(/\/+$/, '')
const isAdmin = path === '/admin'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>{isAdmin ? <AdminPage /> : <App />}</React.StrictMode>,
)
