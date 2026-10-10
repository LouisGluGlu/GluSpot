"use strict";
const fs=require("node:fs"),path=require("node:path"),crypto=require("node:crypto");
const runtime=path.resolve(process.argv[2] || "");
const lock=JSON.parse(fs.readFileSync(path.join(runtime,"package-lock.json"),"utf8"));
const pinned={"@neteasecloudmusicapienhanced/api":"4.41.1","@yakult-green-tea/qq-music-api":"3.1.4","kugoumusicapi":"1.6.0"};
function hash(file){return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");}
for(const [name,version] of Object.entries(pinned)){
 const manifest=JSON.parse(fs.readFileSync(path.join(runtime,"node_modules",name,"package.json"),"utf8"));
 if(manifest.version!==version || manifest.license!=="MIT")throw new Error("Pinned adapter license/version mismatch");
 const text=fs.readFileSync(path.join(runtime,"node_modules",name,"LICENSE"),"utf8");
 if(!text.includes("Permission is hereby granted"))throw new Error("Adapter MIT license missing");
}
const notices=path.join(runtime,"licenses");fs.mkdirSync(notices,{recursive:true});
const nodeLicense=path.join(notices,"Node-LICENSE.txt");
const nodeLicenseText=fs.readFileSync(nodeLicense,"utf8");
if(!nodeLicenseText.includes("Node.js") || !nodeLicenseText.includes("Permission is hereby granted") || nodeLicenseText.length<1000)throw new Error("Node release license/notices missing");
const packageRecords=[];
for(const [key,value] of Object.entries(lock.packages || {})){
 if(!key.startsWith("node_modules/"))continue;
 const location=path.join(runtime,key);if(!fs.existsSync(location))continue;
 const licenseNames=fs.readdirSync(location).filter(name=>/^(?:licen[sc]e|copying|notice|attribution)(?:\.|$)/i.test(name) && fs.statSync(path.join(location,name)).isFile());
 const files=[];
 for(const name of licenseNames){const source=path.join(location,name);const output=path.join(notices,key.replaceAll("/","_")+"_"+name);fs.copyFileSync(source,output);files.push({name,sha256:hash(source)});}
 packageRecords.push({path:key,version:value.version,license:value.license || "see retained package metadata",integrity:value.integrity || "",resolved:value.resolved || "",license_files:files});
}
fs.writeFileSync(path.join(runtime,"SOURCE.json"),JSON.stringify({product:"GluSpot",bridge:"native/online_music/bridge.cjs",node:{version:process.version,sha256:hash(path.join(runtime,"node.exe")),license_file:"licenses/Node-LICENSE.txt",license_url:`https://raw.githubusercontent.com/nodejs/node/${process.version}/LICENSE`,license_sha256:hash(nodeLicense)},lock_sha256:hash(path.join(runtime,"package-lock.json")),packages:packageRecords},null,2)+"\n");
console.log(JSON.stringify({verified_adapters:Object.keys(pinned),retained_packages:packageRecords.length,retained_notice_files:fs.readdirSync(notices).length}));
