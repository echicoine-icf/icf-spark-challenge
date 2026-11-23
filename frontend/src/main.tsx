import React from 'react';
import { createRoot } from 'react-dom/client';
import ChatApp from './ChatApp';
import './styles.css';
import './brand.css';

createRoot(document.getElementById('root')!).render(<React.StrictMode><ChatApp /></React.StrictMode>);
