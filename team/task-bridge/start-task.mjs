import fs from 'node:fs';
import {FeishuTaskBridgeRuntime} from '@iluolyx/aamp-feishu-bridge/dist/task/runtime.js';
const {taskDir}=JSON.parse(fs.readFileSync('runtime/paths.json'));
const config=JSON.parse(fs.readFileSync(taskDir+'/config.json'));
const runtime=new FeishuTaskBridgeRuntime(config,{configDir:taskDir});
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,async()=>{await runtime.stop();process.exit(0)});
await runtime.start();
