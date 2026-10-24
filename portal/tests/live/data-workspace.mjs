// SPDX-License-Identifier: GPL-3.0-or-later
// Opt-in real local data check. Credentials stay in ignored runtime state.
import { chromium,expect } from '@playwright/test';
import { readFile,writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const out=new URL('../../../.tmp/data-workspace/',import.meta.url);
const demo=JSON.parse(await readFile(new URL('demo-user.json',out),'utf8'));
const browser=await chromium.launch({channel:'chrome',headless:true});
let phase='login',page;
try {
 const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000}});page=await context.newPage();const calls=[];
 page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/v1/data-workspace/'))calls.push({path:new URL(r.url()).pathname,method:r.method()});});
 const count=p=>calls.filter(c=>c.path.endsWith(p)).length;
 async function clickResponse(label,path,status=200){const p=page.waitForResponse(r=>r.url().endsWith(path)&&r.status()===status);await page.getByRole('button',{name:label,exact:true}).click();return (await p).json();}
 await page.goto('http://127.0.0.1:5174/#data');await page.locator('#username').fill(demo.username);await page.locator('#password').fill(demo.password);await page.locator('#kc-login').click();
 await expect(page.getByRole('heading',{name:'Tổng quan vận hành',exact:true})).toBeVisible({timeout:30000});await expect(page.getByRole('button',{name:'Áp dụng',exact:true})).toBeEnabled({timeout:30000});
 expect(calls.filter(c=>c.method==='POST')).toHaveLength(0);
 phase='logistics';await page.getByLabel('Nội dung',{exact:true}).selectOption({label:'Vận chuyển'});await page.getByLabel('Khoảng xem',{exact:true}).selectOption('365');
 await page.waitForTimeout(650);expect(count('/overview')).toBe(0);
 const logistics=await clickResponse('Áp dụng','/overview');await expect(page.locator('.dw-metric strong').first()).not.toHaveText('—',{timeout:60000});expect(logistics.analytics.quality.missing_periods).toBeGreaterThan(0);
 await expect(page.getByRole('alert')).toHaveCount(0);await page.screenshot({path:fileURLToPath(new URL('ux-logistics.png',out))});
 phase='sales';await page.getByLabel('Nội dung',{exact:true}).selectOption({label:'Nhu cầu bán hàng'});await page.getByLabel('Khoảng xem',{exact:true}).selectOption('90');
 const sales=await clickResponse('Áp dụng','/overview');expect(sales.comparison_entities.length).toBeGreaterThan(1);expect(sales.portfolio.rows.length).toBeGreaterThan(1);expect(sales.capacity_summary.rows.length).toBeGreaterThan(0);await expect(page.locator('.dw-context')).toContainText(sales.entity,{timeout:60000});await expect(page.getByRole('button',{name:'Áp dụng',exact:true})).toBeEnabled();await page.screenshot({path:fileURLToPath(new URL('insights-dashboard.png',out))});
 await expect(page.getByRole('button',{name:'Cấu hình dữ liệu',exact:true})).toHaveCount(0);
 phase='security';await page.getByRole('button',{name:'Đã xác thực',exact:true}).click();await expect(page.getByRole('dialog')).toContainText('Authorization Code và PKCE');await page.keyboard.press('Escape');
 phase='forecast';await page.getByRole('button',{name:'Dự đoán số liệu',exact:true}).first().click();const forecast=await clickResponse('Tạo dự đoán','/forecasts',201);const clickedDay=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());expect(forecast.points[0].timestamp).toBe(clickedDay);expect(forecast.points.length).toBe(14);expect(forecast.evidence.origin_policy).toBe('request_date');expect(Object.keys(forecast.model.entities[sales.entity].validation)).toHaveLength(2);
 await expect(page.getByLabel('Số lượng nhập thêm',{exact:true})).toBeVisible({timeout:60000});expect(count('/scenarios')).toBe(0);const inference=count('/forecasts');
 const date=new Date(forecast.points[0].timestamp+'T00:00:00Z');date.setUTCDate(date.getUTCDate()+2);const day3=date.toISOString().slice(0,10);
 phase='day3';await page.getByLabel('Số lượng nhập thêm',{exact:true}).fill('50');await page.getByLabel('Ngày dự kiến hàng về',{exact:true}).fill(day3);await page.waitForTimeout(650);expect(count('/scenarios')).toBe(0);
 const a=await clickResponse('Áp dụng phương án','/scenarios');await expect(page.getByRole('button',{name:'Lưu phương án',exact:true})).toBeEnabled({timeout:60000});
 const idx=a.proposed.series.findIndex(p=>p.day===day3);expect(a.proposed.series[0].net_position).toBe(a.baseline.series[0].net_position);expect(a.proposed.series[idx].net_position-a.baseline.series[idx].net_position).toBeCloseTo(50);
 phase='day6';date.setUTCDate(date.getUTCDate()+3);await page.getByLabel('Ngày dự kiến hàng về',{exact:true}).fill(date.toISOString().slice(0,10));await page.waitForTimeout(650);expect(count('/scenarios')).toBe(1);expect(count('/forecasts')).toBe(inference);
 const b=await clickResponse('Áp dụng phương án','/scenarios');await expect(page.getByRole('button',{name:'Lưu phương án',exact:true})).toBeEnabled({timeout:60000});expect(b.proposed.series[idx].net_position).toBe(b.baseline.series[idx].net_position);expect(b.evidence.snapshot_id).toBe(a.evidence.snapshot_id);
 await page.locator('.dw-main').evaluate(e=>{e.scrollTop=0;});await page.screenshot({path:fileURLToPath(new URL('insights-studio.png',out))});
 phase='cautious';await page.locator('.dw-advanced summary').click();await page.getByLabel('Mức chuẩn bị',{exact:true}).selectOption('p90');await page.waitForTimeout(650);expect(count('/scenarios')).toBe(2);const cautious=await clickResponse('Áp dụng phương án','/scenarios');expect(cautious.evidence.assumptions.demand_quantile).toBe('p90');expect(cautious.baseline.metrics.forecast_demand).toBeGreaterThanOrEqual(b.baseline.metrics.forecast_demand);expect(count('/forecasts')).toBe(inference);await expect(page.getByRole('button',{name:'Lưu phương án',exact:true})).toBeEnabled();
 phase='save';await page.getByRole('button',{name:'Lưu phương án',exact:true}).click();await expect(page.getByRole('status')).toContainText('Đã lưu phương án',{timeout:30000});
 phase='mobile';await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:fileURLToPath(new URL('insights-mobile.png',out))});
 await writeFile(new URL('insights-browser-evidence.json',out),JSON.stringify({status:'PASS',checks:['real SSO and consumer role','no data reads before Apply','sparse Logistics and Sales','real persisted forecast','plan edits wait for Apply','day3/day6 use same snapshot','no forecast refit','scenario saved','daily P90 cautious planning','request-day origin','multiple entities','Sales Warehouse Logistics composition','chronological model selection','mobile fits'],logistics_quality:logistics.analytics.quality,model:forecast.model,forecast_start:forecast.points[0].timestamp,cross_source_rows:sales.portfolio.rows.length,capacity_carriers:sales.capacity_summary.rows.length,forecasts:inference,calls:calls.length},null,2));console.log('PASS: live multi-source dashboard, request-day forecast, validated model choice and P50/P90 planning');
} catch(e) {if(page)await page.screenshot({path:fileURLToPath(new URL('ux-browser-failure.png',out))}).catch(()=>{});await writeFile(new URL('ux-browser-private-error.txt',out),`${phase}: ${String(e)}`);console.error(`FAIL at ${phase}; inspect private evidence`);process.exitCode=1;}finally{await browser.close();}
