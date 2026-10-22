# 第三方组件与素材

GluSpot 自有源码及文档按根目录 [MIT License](LICENSE) 授权。下列第三方组件、字体、图标和展示素材保留各自的版权与许可证，根目录 MIT 不替代其授权。

## 随源码提供的组件

| 组件 | 许可与声明 |
| --- | --- |
| airplay2-sender-cpp | Apache-2.0；保留其 [LICENSE](third_party/airplay2-sender-cpp/LICENSE)、[NOTICE](third_party/airplay2-sender-cpp/NOTICE) 和 [第三方声明](third_party/airplay2-sender-cpp/licenses/THIRD-PARTY-NOTICES.txt)，其中包含 pyatv、pair_ap、Mbed TLS 和 ed25519 的声明 |
| 在线音乐适配器 | 固定版本与使用边界见 [native/online_music](native/online_music)；相关许可在 [licenses](native/online_music/licenses) 中，构建时继续保留依赖自身许可 |

## 字体

| 字体 | 许可与来源 |
| --- | --- |
| Source Han Sans SC Heavy | SIL Open Font License 1.1；[许可与来源记录](assets/fonts/SourceHanSans) |
| Noto Serif SC ExtraBold | SIL Open Font License 1.1；[随附许可](assets/fonts/NotoSerifSC/LICENSE.txt)，[上游](https://github.com/notofonts/noto-cjk) |
| 江城斜宋体 700W / 900W | SIL Open Font License 1.1，基于思源宋体的衍生字体；[随附许可](assets/fonts/JiangChengXieSong/LICENSE.txt)，[作者项目](https://github.com/liupeng1199/JiangChengXieSong) |
| 阿里巴巴普惠体 Bold / Heavy | 字体版本 1.10，版权归阿里巴巴（中国）有限公司，适用原厂字体授权；[版权与使用说明](assets/fonts/AlibabaPuHuiTi/NOTICE.txt)，[官方网站](https://www.alibabafonts.com/)。不是按 GluSpot 的 MIT 许可重新授权的字体 |

Windows 系统字体由系统提供，不随本仓库分发。品牌字形的生成来源见 [品牌说明](assets/brand/README.md)。

## 运行时依赖

依赖列表见 [requirements.txt](requirements.txt)。PySide6 / Qt、zeroconf、ToJyutping、NumPy、Pillow、SoundCard、winsdk 及其他依赖遵循其自身许可；它们不会因 GluSpot 采用 MIT 而改变授权。Windows 发行包保留已打包依赖的许可和适用的源码资料。

## 界面素材与演示

图标素材的来源和边界分别见 [播放控件](assets/music_controls/README.md)、[音频设备](assets/audio_outputs/README.md)、[音乐来源](assets/music_sources/README.md) 与 [窗口控件](assets/window_controls/README.md) 的说明。第三方商标仍属于各自权利人，不代表赞助或官方合作。

README 的界面演示用于说明软件功能；其中的歌曲、歌词、专辑封面及音乐平台标识属于各自权利人，不纳入本项目自有代码的 MIT 授权。演示素材不包含登录二维码或账号凭据。
