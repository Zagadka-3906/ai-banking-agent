/** Editable contest deck, authored with the supplied Artifact Tool runtime.
 * Set RUNTIME_NODE_MODULES and PRESENTATIONS_SKILL_DIR to the installed bundle.
 * Usage: node scripts/build_contest_deck.mjs build/contest-upgrade/delivery
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const root = process.cwd();
const modules = process.env.RUNTIME_NODE_MODULES;
const skill = process.env.PRESENTATIONS_SKILL_DIR;
if (!modules || !skill) throw new Error('Supplied Artifact Tool runtime and Presentations skill paths are required');
const { Presentation, PresentationFile } = await import(pathToFileURL(path.join(modules, '@oai/artifact-tool/dist/artifact_tool.mjs')));
const { finalizePresentation } = await import(pathToFileURL(path.join(skill, 'container_tools/artifact_tool_utils.mjs')));
const output = path.resolve(process.argv[2] || 'build/contest-upgrade/delivery');
const privateDir = path.join(root, 'build/contest-upgrade', `deck-private-${Date.now()}`);
await fs.mkdir(output, { recursive: true });
await fs.mkdir(privateDir, { recursive: true });
const evidence = JSON.parse(await fs.readFile(path.join(root, 'build/contest-upgrade/delivery-evidence.json'), 'utf8'));
const benchmark = JSON.parse(await fs.readFile(path.join(root, 'build/contest-upgrade/intent-benchmark-final/benchmark.json'), 'utf8'));
const comparison = JSON.parse(await fs.readFile(path.join(root, 'build/contest-upgrade/conversation-comparison/comparison.json'), 'utf8'));
const font = 'Microsoft YaHei';
const ink = '#192641', muted = '#596477', bg = '#FAF7F1', purple = '#6550A6';
const deck = Presentation.create({ slideSize: { width: 1280, height: 720 } });
function text(slide, value, x, y, w, h, size = 28, color = ink, bold = false) {
  const shape = slide.shapes.add({ geometry: 'textbox', position: { left:x, top:y, width:w, height:h }, fill:'none', line:{fill:'none',width:0} });
  shape.text = value;
  shape.text.style = { typeface:font, fontSize:size, color, bold };
  return shape;
}
function slide(title, notes) {
  const s = deck.slides.add(); s.background.fill = bg;
  text(s,title,66,48,1148,76,46,ink,true);
  text(s,`${deck.slides.items.length} / 8`,1130,662,90,25,19,muted);
  s.speakerNotes.text = notes;
  return s;
}
function node(s,label,x,y,w=270,h=100) {
  const box = s.shapes.add({ geometry:'rect',position:{left:x,top:y,width:w,height:h},fill:'#ECE7F4',line:{fill:purple,width:1.5} });
  box.text = label; box.text.style={typeface:font,fontSize:27,color:ink}; return box;
}
function connect(s,a,b,from='right',to='left') { s.shapes.connect(a,b,{kind:'straight',fromSide:from,toSide:to,line:{fill:purple,width:2},tail:{type:'triangle'}}); }
function table(s,values,top,height,widths) {
  const t=s.tables.add({ rows:values.length,columns:values[0].length,left:66,top,width:1148,height,values,columnWidths:widths });
  for(let r=0;r<values.length;r++)for(let c=0;c<values[r].length;c++){
    const cell=t.getCell(r,c);cell.fill=r===0?ink:(r%2?'#F0ECF6':bg);
    cell.text.style={typeface:font,fontSize:r===0?26:25,color:r===0?'#FFFFFF':ink,bold:r===0};
  }
  return t;
}
let s=slide('安言', '用时 40 秒。介绍作品名称与目标。Android 是自然语言入口，Mock Core 持有虚构账本并独立授权。作品不连接真实银行。来源：README.md、docs/技术文档.md。');
text(s,'AI 银行智能体',70,195,650,90,64,ink,true);
text(s,'自然语言办理需求\n逐笔确认资金操作',74,324,680,140,39,purple);
text(s,'Android 应用 / 本机 Mock Core / 飞书只读接入',74,533,970,45,25,muted);
text(s,'所有账户、商品和交易均为虚构演示数据',74,593,990,34,23,muted);
if(evidence.coverScreenshot)s.images.add({blob:await fs.readFile(path.resolve(evidence.coverScreenshot)),contentType:'image/png',alt:'本轮真实 Android 应用截图',fit:'contain',position:{left:910,top:142,width:250,height:474}});

s=slide('六类业务场景', '用时 60 秒。说明六类已有实现，随后聚焦查询、转账和生日任务。订阅候选不等于合同，Mock 订单不等于真实履约。来源：README.md、docs/技术文档.md。');
const scenarios=[['01 智能转账','联系人、预约与 AA 请求'],['02 账单分析','日期、分类、异常证据'],['03 理财操作','测评、对比、模拟申赎'],['04 卡片管理','限额、挂失与设备验证'],['05 订阅管理','周期候选、提醒与取消'],['06 跨场景联动','生日锁资、提醒与订单']];
scenarios.forEach(([title,body],i)=>{const x=70+(i%2)*580,y=180+Math.floor(i/2)*145;text(s,title,x,y,520,45,32,purple,true);text(s,body,x,y+54,520,50,27);});

s=slide('系统架构与执行边界', '用时 70 秒。沿着客户端、Gateway、编排、Core 讲一笔请求。模型只能提取受约束意图，Core 重算风险并校验授权。银行私钥保存在 Android Keystore。图为可编辑架构图。来源：docs/技术文档.md。');
const android=node(s,'Android\n文本 / 端侧语音',65,190),gateway=node(s,'Agent Gateway\n会话 / SSE / 审批',505,190),core=node(s,'Mock Core\n授权 / 唯一记账',945,190);
connect(s,android,gateway);connect(s,gateway,core);
const model=node(s,'LangGraph + 模型\n意图校验 / 任务编排',505,400),store=node(s,'虚构账本与审计\n回执 / 幂等 / 设备',945,400);
connect(s,gateway,model,'bottom','top');connect(s,core,store,'bottom','top');
text(s,'飞书私聊只读；资金办理回 Android 确认',70,568,1060,48,28,muted);

s=slide('意图理解的实测对比', '用时 75 秒。同一份 24 项合成表达分别运行规则与真实模型模式。包含含糊金额、转账新表达、卡片、理财及注入。只评测解析，不能称为交易完成率。小样本不代表所有自然语言。来源：tests/data/intent_benchmark.json、benchmark.json，其 SHA256 随报告提供。');
const rules=benchmark.results.rules.summary, modelResult=benchmark.results.model.summary;
table(s,[['模式','通过 / 实测','P95 响应','模型调用 / 返回'],['规则解析',`${rules.passed} / ${rules.evaluated}`,`${rules.p95_ms} ms`,'0 / 0'],['真实模型',`${modelResult.passed} / ${modelResult.evaluated}`,`${modelResult.p95_ms} ms`,`${modelResult.model_calls} / ${modelResult.model_returns}`]],180,245,[250,260,300,338]);
text(s,`多轮端到端：规则 ${comparison.results.rules.cases.PASS}/12，联网 ${comparison.results.live_hybrid.cases.PASS}/12`,72,470,1130,55,33,purple,true);
text(s,'上表仅解析；端到端同时核对页面、审批与 Core 状态',72,549,1130,55,27,muted);
text(s,'固定合成回归，非独立盲测；规则未识别项保留为未通过',72,607,1130,35,23,muted);

s=slide('三级权限与强验证', '用时 95 秒。绿色直接查询。黄色确认。红色确认、验证码和登记设备签名。演示一次性验证码绑定审批，最长 60 秒，重新发码不重置失败次数；演示发码在应用内显示，不是真实短信。固定码仅保留兼容测试，推荐启用动态模式。来源：security.py、docs/安全自评报告.md。');
table(s,[['等级','操作示例','执行要求'],['绿色','余额、账单、收益查询','登录后自动查询'],['黄色','当日累计 ≤1000 元转账','摘要核对、明确确认'],['红色','超额转账、理财申购、挂失','确认、验证码、设备签名']],169,325,[170,468,510]);
text(s,'单次授权 / 到期失效 / 执行前复核 / 幂等记账',72,548,1120,53,29,purple,true);

s=slide('复杂任务的暂停与接管', '用时 80 秒。以生日任务说明确定性 DAG。用户确认锁资后生成提醒和订单草稿，资金任务到期仍等待确认。请求人工处理会暂停关联待执行分支，正在执行时先核对结果。已完成步骤不自动撤销，锁资不自动释放。本机没有在线客服。来源：scheduler.py、main.py、ScenarioTaskLifecycleUiTest.kt。');
const reserve=node(s,'确认并锁定资金',70,285,300,95),reminder=node(s,'到期提醒',800,155,410,85),flowers=node(s,'鲜花草稿 / 等待确认',800,285,410,85),cake=node(s,'蛋糕草稿 / 等待确认',800,415,410,85);
connect(s,reserve,reminder);connect(s,reserve,cake);connect(s,reserve,flowers);
text(s,'请求人工处理：先验证关联任务，再一次性暂停分支',72,520,1130,50,29,purple,true);
text(s,'已完成回执保留；锁资与退款需分别核对',72,581,1130,44,26,muted);

s=slide('可复现验证', '用时 75 秒。报告本轮实际执行结果，来源 delivery-evidence.json。设备测试包含账号切换、迟到成功与错误、系统认证回调、接管及语音回填隔离。只将无失败、无跳过且结果完整的运行计为通过。CI 配置已提交，远端执行结果单独确认。');
text(s,`${evidence.pythonTests} 项`,70,185,530,95,62,purple,true);text(s,'Python 自动化测试通过',74,291,570,47,30);
text(s,`${evidence.jvmTests} 项`,690,185,500,95,62,purple,true);text(s,'Android JVM 测试通过',694,291,520,47,30);
text(s,`API 35：${evidence.api35} 项    API 37：${evidence.api37} 项`,74,410,1120,64,38,ink,true);
text(s,'lint 无问题，Debug / Release 构建通过',74,506,1120,52,30);
text(s,'失败、跳过、重复及缺失结果均不计通过',74,580,1120,45,25,muted);

s=slide('当前边界与交付', '用时 65 秒。解释单机 Mock 与生产系统差距。飞书真实验收失败，租户令牌接口返回 21001，需要应用管理员或平台处理；不把监听线程启动视为联通。人员体验结果待收到真实导出记录。端侧语音需支持的设备，模拟器测试不证明真实声音识别效果。GitHub 地址是提交源码入口。');
text(s,'单机 Mock：虚构账本与本地服务',72,181,1110,56,33,ink,true);
text(s,'端侧语音需设备支持；没有在线人工客服\n进程资源限额不等于完整系统隔离\n飞书平台策略 21001 待解除；真实用户记录待回收',72,279,1130,210,29,muted);
text(s,'github.com/Zagadka-3906/ai-banking-agent',72,554,1130,60,31,purple,true);
for(let i=0;i<deck.slides.items.length;i++){
  const png=await deck.export({slide:deck.slides.items[i],format:'png',scale:1});
  await fs.writeFile(path.join(privateDir,`slide-${i+1}.png`),new Uint8Array(await png.arrayBuffer()));
}
const candidate=path.join(privateDir,'candidate.pptx');
await (await PresentationFile.exportPptx(deck)).save(candidate);
const finalPath=path.join(output,'安言答辩.pptx');
await finalizePresentation({workspaceDir:root,candidatePath:candidate,finalPath,
  pythonExecutable:process.env.RUNTIME_PYTHON,
  integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit','--require-native-table-slide','4','--require-native-table-slide','5'],
  explicitTotalSlideCount:8,requiredNativeTableOwnerSlides:[4,5],requiredNativeChartOwnerSlides:[],
  fontPolicy:{basis:'design',families:[font]},verifyArtifactToolImport:true,
  receiptPath:path.join(privateDir,'validation.json')});
console.log('Final deck:',finalPath);
