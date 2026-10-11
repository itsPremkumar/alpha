/**
 * Sitemap generation script for Alpha frontend
 * Generates sitemap.xml for SEO
 */

import fs from 'fs';
import path from 'path';

const BASE_URL = 'https://alpha.itsPremkumar.com';

const routes = [
  { path: '/', changefreq: 'weekly', priority: 1.0 },
  { path: '/chat', changefreq: 'daily', priority: 0.9 },
  { path: '/settings', changefreq: 'monthly', priority: 0.5 },
  { path: '/projects', changefreq: 'weekly', priority: 0.7 },
  { path: '/bots', changefreq: 'weekly', priority: 0.7 },
  { path: '/board', changefreq: 'weekly', priority: 0.7 },
  { path: '/workflows', changefreq: 'weekly', priority: 0.7 },
  { path: '/messages', changefreq: 'daily', priority: 0.7 },
  { path: '/war-room', changefreq: 'weekly', priority: 0.7 },
  { path: '/vitals', changefreq: 'daily', priority: 0.6 },
  { path: '/reliability', changefreq: 'weekly', priority: 0.6 },
  { path: '/intelligence', changefreq: 'weekly', priority: 0.6 },
  { path: '/sentinel', changefreq: 'weekly', priority: 0.6 },
  { path: '/effects', changefreq: 'weekly', priority: 0.6 },
  { path: '/mods', changefreq: 'weekly', priority: 0.6 },
  { path: '/subagents', changefreq: 'weekly', priority: 0.6 },
  { path: '/apex', changefreq: 'weekly', priority: 0.6 },
  { path: '/intelligence', changefreq: 'weekly', priority: 0.6 },
  { path: '/peers', changefreq: 'weekly', priority: 0.6 },
];

function generateSitemap() {
  const today = new Date().toISOString().split('T')[0];
  
  const urls = routes.map(route => {
    const lastmod = new Date().toISOString().split('T')[0];
    return `  <url>
    <loc>${BASE_URL}${route.path}</loc>
    <lastmod>${lastmod}</lastmod>
    <changefreq>${route.changefreq}</changefreq>
    <priority>${route.priority}</priority>
  </url>`;
  }).join('\n');

  const sitemap = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml"
        xmlns:mobile="http://www.google.com/schemas/sitemap-mobile/1.0"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
${urls}
</urlset>`;

  const outputPath = path.join(process.cwd(), 'public', 'sitemap.xml');
  fs.writeFileSync(outputPath, sitemap);
  console.log(`Sitemap generated at ${outputPath}`);
}

generateSitemap();