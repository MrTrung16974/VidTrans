# Prompt phim AI cổ trang / tiên hiệp Trung Quốc

Nguồn: [YouMind, video prompts](https://youmind.com/vi-VN/prompts/video/cinematic-showcase), gồm các mục *Mực Tàu*, *Phim ngắn*, *Nhân vật*, *Điện ảnh chân thực*. Lấy ngày 2026-10-26.
Đã lọc chỉ giữ prompt dành cho model tạo video (Seedance, Grok), bỏ các prompt Three.js/code. Xếp theo mức độ chân thực, giống phim người đóng.

Với Seedance (model của ByteDance), nên dán **bản gốc tiếng Trung** nếu có. Model hiểu bản này chính xác hơn bản dịch.

## Công thức chung rút ra từ các prompt tốt

1. **Dòng phong cách đầu tiên:** `东方古装写实电影质感` (cổ trang phương Đông, chất điện ảnh chân thực), `真实皮肤、发丝、布料、金属质感` (da, tóc, vải, kim loại thật), `低饱和暗调，强烈明暗对比` (bão hòa thấp, tương phản sáng tối mạnh), `胶片级色调` (màu phim).
2. **Cấm “vẻ AI”:** `禁CG感、塑料感` (cấm cảm giác CG, da nhựa).
3. **Khóa nhân vật / đạo cụ / cảnh** bằng ảnh tham chiếu: `【人物锁定】女主A：图1…`, `【道具锁定】`, `【场景锁定】`. Ghi rõ vị trí không gian của từng người.
4. **Phân cảnh theo mốc giây:** mỗi cảnh ghi cỡ cảnh, chuyển động máy (dolly, lia vòng cung, rack-focus), nội dung, thoại, và điều phải giữ liên tục.
5. **Âm thanh:** chỉ giữ âm thanh môi trường (bước chân vọng, vải sột soạt, nến lách tách), không nhạc nền, không phụ đề.
6. **Ràng buộc phủ định ở cuối:** không chữ hoặc watermark, không yếu tố hiện đại, không nhân bản nhân vật, không đổi mặt giữa các nhân vật, không slow-motion lê thê, không dị tật thừa chi.
7. **Diễn xuất tiết chế:** không biểu cảm phóng đại, không phép thuật cường điệu. Đây là điểm khiến video trông giống phim thật.

## 1. Cung điện đêm khuya: lật mặt quyền lực
**Model:** Seedance 2.0 · 10s · cần 3 ảnh nhân vật · chân thực nhất: tông tối, độ bão hòa thấp, có rack-focus · [link](https://youmind.com/vi-VN/video-prompts/seedance-ancient-palace-reveal-13049)

**Bản gốc (tiếng Trung):**

```text
东方古装写实电影质感；深夜大殿冷调；真实皮肤、发丝、布料、金属质感；低饱和暗调，强烈明暗对比；悬疑权谋、压迫氛围；禁CG感、塑料感；仅保留环境声：殿内脚步回声、衣物摩擦声、烛火轻响
【人物锁定】女主A：图1；女配B：图2；女配C：图3
【道具锁定】长剑一柄（先在B手中，后被A取过横于肩头）
【场景锁定】深夜王府议事大殿：石质地面、殿柱、高窗、烛台宫灯、正中主位高背椅
【空间关系】C站殿中偏右；B伏在镜头前景（开场完全失焦）；A在殿内正中深处的主位，前三镜不入画
【空间逻辑】权力层级由近及远逐级揭开：镜头横移加后撤升起，空间一层层拓展——先C（殿中）、再B（前景伏兵）、后A夺权居中；焦点顺序铁律：C→B→A→全景
【光影氛围】冷月光从高窗斜入，烛火点暖；整体冷调低饱和，强明暗对比

【分镜】
【镜头01】【时长】0–3秒【景别】中景起幅【运镜】平稳起幅后缓慢向左弧形横移【画面内容】大殿内，C斜身站立，低头沉思；随即高傲地抬眼直视镜头，眼神不屑而疏离；前景预留一片失焦区域（B的伏位），焦点稳定锁定C，景深层次分明【连续性锁定】开场：C登场；B只是一团失焦影子，不露脸

【镜头02】【时长】3–5秒【景别】贴脸大特写【运镜】持续左移并极速前景拉焦【画面内容】原本完全失焦的前景中，B猛然转头，低角度贴脸大特写撞满画面，眼神冷酷蔑视，直勾勾锁定镜头；背景中的C彻底虚化，空间拉扯感强烈【连续性锁定】拉焦切境：焦点从C转到B；B是伏兵，不是主角

【镜头03】【时长】5–7秒【景别】中景【运镜】迅速向后拉远并持续左右轻摇【画面内容】B的贴脸偏到一侧，随镜头后拉缩小至中景，B按剑伫立；A从画面左侧突然快步位移入画，占据绝对景线，伸手从B手中取过长剑，抬于肩头，嘴角带着高高在上的嘲弄微笑俯视镜头；焦点最终锁定A的正面部【连续性锁定】夺权完成：剑到A手、景线归A；B退为陪衬

【镜头04】【时长】7–10秒【景别】大全景落幅【运镜】斜向后撤并持续升起【画面内容】镜头不断斜向后撤升起，画面空间逐级拓展；镜头完全拉远，形成权力定格大全景：冷月高窗、烛火摇曳，强明暗对比，低饱和暗调，压迫感拉满【连续性锁定】收束：真正的掌权者最后才露面；三人权力层级定格

【负面约束】严禁任何形式的背景音乐、无字幕；无多余文字、无水印、无现代元素（无西装、无眼镜、无汽车、无球棒）、无人物复制、无解剖错误、无身份漂移、无A变脸成B或C、无焦点顺序错误、无长剑凭空消失、无慢动作拖沓；A、B、C全程站位不移动
```

**Bản tiếng Việt:**

```text
Trang phục cổ trang phương Đông, chất lượng điện ảnh chân thực như ảnh chụp; đại sảnh đêm khuya tông màu lạnh; kết cấu da/tóc/vải/kim loại chân thực; bảng màu tối độ bão hòa thấp tương phản sáng tối mạnh mẽ; không khí kịch tính hồi hộp đầy áp lực; cấm hiệu ứng CG/thẩm mỹ nhựa; chỉ dùng âm thanh môi trường: tiếng bước chân vọng lại, tiếng sột soạt của y phục, tiếng nến nổ lách tách
[Khóa Nhân Vật] Nữ chính A: Hình 1; Vai phụ B: Hình 2; Vai phụ C: Hình 3
[Khóa Đạo Cụ] Một thanh trường kiếm duy nhất (ban đầu do B cầm, sau chuyển cho A, gác ngang vai)
[Khóa Cảnh] Phòng hội đồng hoàng gia đêm khuya: sàn đá, cột trụ, cửa sổ cao, chân nến, đèn lồng, ngai vàng đặt trên bục cao ở trung tâm
[Quan Hệ Không Gian] C đứng chếch về phía giữa bên phải đại sảnh; B ngồi xổm ở tiền cảnh ban đầu hoàn toàn mất nét; A ngự trên ngai vàng sâu ở trung tâm, chưa xuất hiện trong ba cảnh quay đầu tiên
[Logic Không Gian] Tôn ti quyền lực được hé lộ dần từ gần ra xa qua cú máy dolly lùi và hướng lên theo chiều ngang, mở rộng không gian từng lớp một—trước tiên là C (giữa sảnh), rồi đến B (phục kích tiền cảnh), cuối cùng là A chiếm lĩnh trung tâm; thứ tự lấy nét nghiêm ngặt: C→B→A→toàn cảnh
[Ánh Sáng & Bầu Không Khí] Ánh trăng lạnh hắt xiên qua các khung cửa sổ cao, điểm xuyết ánh nến ấm áp; tổng thể ánh sáng lạnh, độ bão hòa thấp, tương phản cao

[Kịch Bản Phân Cảnh]
[Cảnh 01][Thời Lượng] 0–3s [Khuôn Hình] Bắt đầu bằng cỡ cảnh trung [Chuyển Động] Mượt mà lúc đầu, sau đó lia máy theo vòng cung chậm sang trái [Nội Dung] Bên trong đại sảnh, C đứng xoay người, cúi đầu trầm tư; đột nhiên ngước mắt lên, ánh nhìn kiêu ngạo trực diện đầy vẻ khinh miệt và xa cách; tiền cảnh dành riêng một vùng mất nét (vị trí của B), tiêu điểm giữ ổn định vào C tạo độ sâu trường ảnh rõ rệt [Khóa Tính Liên Tục] Mở đầu: C xuất hiện; B chỉ là một bóng mờ nhòe, không rõ mặt

[Cảnh 02][Thời Lượng] 3–5s [Khuôn Hình] Cận cảnh đặc tả khuôn mặt [Chuyển Động] Tiếp tục lia sang trái, chuyển tiêu nhanh ra tiền cảnh [Nội Dung] B ở tiền cảnh vốn đang mất nét bỗng ngoảnh đầu thật nhanh, góc máy thấp cận cảnh đặc tả lấp đầy khuôn hình, ánh mắt lạnh lùng khinh khỉnh khóa chặt ống kính; C ở hậu cảnh hoàn toàn xóa phông (bokeh), tạo sức căng không gian mãnh liệt [Khóa Tính Liên Tục] Chuyển cảnh bằng kỹ thuật rack-focus: tiêu điểm dịch chuyển C→B; B là kẻ phục kích chứ không phải nhân vật chính

[Cảnh 03][Thời Lượng] 5–7s [Khuôn Hình] Cỡ cảnh trung [Chuyển Động] Kéo lùi nhanh, rung lắc nhẹ liên tục theo chiều ngang [Nội Dung] Khuôn mặt B dịch sang một bên, thu nhỏ lại thành cỡ cảnh trung khi máy quay lùi; B đứng thủ thế với kiếm; A bất ngờ sải bước tiến vào từ bên trái, áp đảo khuôn hình, vươn tay qua B lấy thanh trường kiếm, giơ lên gác ngang vai, nở nụ cười nửa miệng ngạo mạn nhìn xuống camera; tiêu điểm cuối cùng khóa chặt khuôn mặt chính diện của A [Khóa Tính Liên Tục] Hoàn tất việc đoạt quyền: kiếm chuyển sang tay A, đạt được sự áp đảo khuôn hình; B bị đẩy xuống làm nền

[Cảnh 04][Thời Lượng] 7–10s [Khuôn Hình] Toàn cảnh hoành tráng để kết thúc [Chuyển Động] Lùi chéo, liên tục nâng lên cao [Nội Dung] Máy quay liên tục lùi theo đường chéo và bay lên cao, mở rộng không gian dần dần; cú kéo máy trọn vẹn thiết lập bức tranh quyền lực đóng băng: cửa sổ đẫm ánh trăng lạnh, nến cháy bập bùng, tương phản gay gắt, bóng tối độ bão hòa thấp tạo cảm giác áp chế tột độ [Khóa Tính Liên Tục] Kết cục: quyền uy thực sự xuất hiện sau cùng; cấu trúc quyền lực bộ ba được định hình rõ nét

[Ràng Buộc Phủ Định] Tuyệt đối không có nhạc nền/phụ đề; không có văn bản thừa/hình mờ/yếu tố hiện đại (không vest/kính/ô tô/bóng chày); không nhân bản nhân vật/lỗi giải phẫu/sai lệch danh tính/mặt A biến thành B hoặc C/vi phạm thứ tự lấy nét/kiếm biến mất/chuyển động chậm lê thê; A/B/C giữ nguyên vị trí cố định xuyên suốt
```

## 2. Khuê phòng: hài gia đình (mèo cam, em gái trong tủ)
**Model:** Seedance 2.0 · 15s · 3 ảnh nhân vật · có thoại theo mốc thời gian, ánh nến ấm · [link](https://youmind.com/vi-VN/video-prompts/seedance-comedy-bedroom-scene-13050)

**Bản gốc (tiếng Trung):**

```text
文生视频提示词：东方古装写实电影质感；闺房烛光暖调；真实皮肤、发丝、布料、雕花木器质感；胶片级色调；轻喜剧；禁CG感、塑料感
【人物锁定】女主A：图1；男主B（赵明成）：图2；女配C（姐妹）：图3；橘猫
【关键设定】C藏在衣橱里
【场景锁定】古风闺房：雕花木床、梳妆台与铜镜、烛台宫灯、雕花大衣橱
【空间关系】A站在床前；B垂头坐在床边；衣橱在房间另一侧；C在衣橱内
【空间逻辑】单场景；A行动路线：床前→转身走向衣橱→开橱门；猫全程不离B肩膀
【光影氛围】烛光暖调，面部受光柔和

【分镜】
【镜头01】【时长】0–3.5秒【景别】中景【运镜】固定【画面内容】A站在床前居高临下地数落；B低头坐在床边【对白】A（约0.5–2秒）："当初成亲，要你十万两聘礼"；A（约2–3.5秒）："是我姐妹借了你九万两"，同时肩上的橘猫用爪子拍了一下B的脑袋【连续性锁定】开场：A主导训话；

【镜头02】【时长】3.5–7秒【景别】中景【运镜】固定【画面内容】A越说越气，手指点着B；B的头垂得更低，猫依旧稳坐肩头【对白】A（约3.5–5秒）："现在又帮你谋了差事"；A（约6–7秒）："赵明成，忘本了是不？"【连续性锁定】A细数闺蜜的功劳；B不还嘴

【镜头03】【时长】7–8.5秒【景别】近景【运镜】固定【画面内容】B低着头，小声认错，肩上的猫面无表情看着前方【对白】B委屈的说（约7.5–8.3秒）："小婉，我错了"【连续性锁定】B认怂；无多余对白

【镜头04】【时长】8.5–12.5秒【景别】中景【运镜】横移跟随【画面内容】A转身大步走向衣橱，边走边骂，在橱门前停住，手搭上橱门【对白】A（约9–11秒）："我不在家，姐妹来家中做客，你居然把她关在衣橱里"；A（约12秒）："你还是人吗？"【连续性锁定】走向衣橱=揭晓倒计时；B在床边不敢动

【镜头05】【时长】12.5–15秒【景别】中景【运镜】固定【画面内容】A一把拉开橱门：C站在衣橱里，尴尬的冲A笑着挥手说：“嗨～”；画面定格【连续性锁定】收束

【负面约束】严禁任何形式的背景音乐；无多余文字、无水印、无人物复制、无解剖错误、无现代元素（无电灯、无现代家具）、无身份漂移、无A变脸成B或C、无猫离开B肩膀、无猫虎纹装变形、严禁串台词、无慢动作拖沓
```

**Bản tiếng Việt:**

```text
Prompt text-to-video: Cổ trang phương Đông, chất lượng điện ảnh chân thực như ảnh chụp; khuê phòng ánh nến tông màu ấm áp; kết cấu da/tóc/vải/gỗ chạm khắc chân thực; chỉnh màu chuẩn phim điện ảnh; hài nhẹ; cấm đồ họa CG/thẩm mỹ nhựa
[Khóa Nhân Vật] Nữ chính A: Hình 1; Nam chính B (Triệu Minh Thành): Hình 2; Vai phụ C (Em gái): Hình 3; Mèo mướp cam
[Bố Cục Chính] C trốn bên trong tủ quần áo
[Khóa Bối Cảnh] Khuê phòng cổ phong: giường gỗ chạm khắc, bàn trang điểm, gương đồng, chân nến, đèn lồng, tủ quần áo chạm khắc lớn
[Quan Hệ Không Gian] A đứng trước giường; B ngồi rũ rượi ở mép giường; tủ quần áo ở phía đối diện; C ở trong tủ
[Logic Không Gian] Một bối cảnh duy nhất; lộ trình của A: trước giường→quay về phía tủ→mở cửa; mèo không bao giờ rời khỏi vai B
[Ánh Sáng & Bầu Không Khí] Ánh nến ấm áp, chiếu sáng khuôn mặt dịu nhẹ

[Kịch Bản Phân Cảnh]
[Cảnh 01][Thời Lượng] 0–3.5s [Khuôn Hình] Trung cảnh [Chuyển Động] Tĩnh [Nội Dung] A đứng sừng sững mắng mỏ từ trên xuống; B ngồi cúi đầu ở mép giường [Thoại] A(~0.5–2s): "Lúc cưới nhau thiếp đòi 10 vạn lạng bạc sính lễ"; A(~2–3.5s): "Trong đó có 9 vạn là do em gái thiếp cho chàng vay" cùng lúc đó chú mèo cam tát một phát vào đầu B [Khóa Liên Tục] Mở đầu: A áp đảo quát mắng;

[Cảnh 02][Thời Lượng] 3.5–7s [Khuôn Hình] Trung cảnh [Chuyển Động] Tĩnh [Nội Dung] A càng thêm tức giận chỉ tay vào mặt B; B cúi đầu thấp hơn, mèo vẫn yên vị trên vai [Thoại] A(~3.5–5s): "Giờ đến cả chức quan của chàng cũng là do sắp đặt"; A(~6–7s): "Triệu Minh Thành đúng là kẻ vong ân bội nghĩa nhỉ?" [Khóa Liên Tục] A kể lể công lao của em gái; B im lặng chịu trận

[Cảnh 03][Thời Lượng] 7–8.5s [Khuôn Hình] Cận cảnh [Chuyển Động] Tĩnh [Nội Dung] B cúi gầm mặt lí nhí xin lỗi, mèo mặt vô cảm nhìn thẳng về phía trước [Thoại] B đầy hối lỗi (~7.5–8.3s): "Tiểu Uyển, ta sai rồi" [Khóa Liên Tục] B đầu hàng; không có thoại phụ

[Cảnh 04][Thời Lượng] 8.5–12.5s [Khuôn Hình] Trung cảnh [Chuyển Động] Lia ngang [Nội Dung] A xoay người bước nhanh về phía tủ quần áo, vừa đi vừa mắng, dừng lại trước cửa tủ, tay đặt lên tay nắm [Thoại] A(~9–11s): "Thiếp vắng nhà, em gái thiếp đến thăm mà chàng lại nhốt nó trong tủ"; A(~12s): "Chàng còn là người nữa không?" [Khóa Liên Tục] Tiến về phía tủ = đếm ngược đến lúc hé lộ sự thật; B chết trân ở mép giường

[Cảnh 05][Thời Lượng] 12.5–15s [Khuôn Hình] Trung cảnh [Chuyển Động] Tĩnh [Nội Dung] A mở tung cánh tủ: C đứng bên trong ngượng nghịu vẫy tay cười nói "Hi~"; đóng băng khung hình [Khóa Liên Tục] Kết thúc

[Ràng Buộc Phủ Định] Tuyệt đối không có nhạc nền; không có văn bản thừa/hình mờ/nhân đôi nhân vật/lỗi giải phẫu/yếu tố hiện đại (không đèn điện/không nội thất hiện đại)/lệch danh tính/mặt A biến thành B hoặc C/mèo rời khỏi vai B/biến dạng hoa văn lông mèo/nhầm lẫn thoại/chuyển động chậm kéo lê
```

## 3. Ứng Long hạ xuống giếng trời, đối mặt nữ nhân áo trắng
**Model:** Seedance 2.5 · 15s · 9:16 · thuần text-to-video, không cần ảnh · [link](https://youmind.com/vi-VN/video-prompts/eastern-dragon-well-confrontation-12791)

**Bản gốc (tiếng Trung):**

```text
青砖天井，珍珠白应龙缓缓下探，月白长袍女子与龙对峙；15 秒竖屏。

时长：15秒
比例：9:16竖屏
形式：纯文生视频，无任何参考图。
风格：东方奇幻写实电影感，慢节奏、压迫感与静谧并存。
无对白、无旁白、无字幕、无界面文字、无音频歌词。

【主体】
一口深邃的青砖圆形天井水井，井壁多层楼阁式砖纹向上延伸。井底浅水映出微光。
一条成年东方应龙（独角、长须、四爪），鳞片为珍珠白带靛青边缘，从井壁上方极慢下探。
井台浅水广台边站着一位成年东方女性，身着月白交领长袍与雾青披帛，长发披散，面朝巨龙，神情镇定。

【镜头与动作】
00:00–00:04：镜头沿浅水广台边缘做极轻微顺时针弧移，同时微微后撤，人物、水面反射、井壁与盘井而下的龙形成缓慢透视变化；严格不做剧烈Orbit。
00:04–00:10：应龙龙头先向下送出，颈部形成柔和弧线，力量沿胸段缓慢传递；近前龙爪略张再收。盘绕井壁的后半龙身做极慢S形延迟波动。翼根膜翼仅轻颤，不展开扑翼。龙口微张吐少量寒雾。井中薄雾盘旋下坠，水面细纹外扩。
00:10–00:15：月白衣女子几乎不动，衣摆与长发被压风后拂。建筑绝对稳定。镜头缓推至女子与龙面对面的特写后定格。

【光影】
天井顶洒下冷青天光，井壁湿润反光；龙鳞与寒雾带细微高光。

【Negative】
参考图依赖，字幕，识别框，HUD，西方飞龙造型，多头，肢体增生，剧烈抖动，瞬移，卡通塑料质感，水印，平台Logo，对白，音乐盖过环境音。
```

**Bản tiếng Việt:**

```text
Thời lượng: 15 giây
Tỷ lệ: 9:16 Dọc
Định dạng: Thuần text-to-video, không dùng ảnh tham chiếu.
Phong cách: Tiên hiệp phương Đông mang hơi hướng điện ảnh chân thực, nhịp độ chậm, sự giao hòa giữa cảm giác ngột ngạt và tĩnh lặng.
Không thoại, không lời bình, không phụ đề, không chữ trên giao diện, không lời bài hát trong âm thanh.

[Chủ thể]
Một giếng nước sân trong hình tròn và sâu được xây bằng gạch xanh, trên thành giếng là các họa tiết gạch kiểu đình đài nhiều tầng vươn lên cao. Lớp nước nông dưới đáy phản chiếu ánh sáng mờ ảo.
Một con Yinglong (Ứng Long) phương Đông trưởng thành (một sừng, râu dài, bốn vuốt), vảy trắng ngọc viền chàm, đang hạ xuống cực kỳ chậm rãi từ phía trên thành giếng.
Đứng trên bệ nước nông ven giếng là một người phụ nữ phương Đông trưởng thành, mặc áo choàng giao lĩnh màu trắng nguyệt bạch cùng dải khăn choàng vai xanh khói, tóc dài xõa tự nhiên, đối diện với con rồng khổng lồ, thần thái điềm tĩnh.

[Góc máy và Hành động]
00:00–00:04: Máy quay di chuyển theo đường vòng cung cực nhẹ theo chiều kim đồng hồ dọc mép bệ nước nông, đồng thời lùi lại một chút. Nhân vật, bóng phản chiếu trên mặt nước, thành giếng và thân rồng uốn lượn men theo giếng tạo nên những thay đổi phối cảnh chậm rãi; TUYỆT ĐỐI KHÔNG dùng hiệu ứng Orbit (xoay máy) giật cục.
00:04–00:10: Đầu Yinglong hạ xuống trước, phần cổ tạo thành một đường cong mềm mại, lực truyền chậm rãi dọc theo phần ngực; hai chi trước hơi xòe ra rồi thu lại. Nửa thân sau của rồng quấn quanh thành giếng thực hiện chuyển động uốn lượn hình chữ S cực chậm có độ trễ. Gốc cánh/màng cánh chỉ rung nhẹ, không xòe ra hay vỗ. Miệng rồng hé mở thở ra một lượng nhỏ sương lạnh. Làn sương mỏng trong giếng xoáy đi xuống, những gợn sóng lăn tăn trên mặt nước lan dần ra ngoài.
00:10–00:15: Người phụ nữ mặc áo trắng nguyệt bạch gần như bất động, tà áo và mái tóc dài bị luồng gió áp hất ngược về phía sau. Kiến trúc giữ nguyên trạng thái hoàn toàn vững chãi. Máy quay từ từ tiến vào cận cảnh người phụ nữ và con rồng đang đối diện nhau, sau đó đóng băng khung hình.

[Ánh sáng]
Ánh sáng xanh da trời lạnh lẽo đổ xuống từ đỉnh khoảng sân, phản chiếu trên những bức tường giếng ẩm ướt; vảy rồng và làn sương lạnh lấp lánh những điểm sáng tinh tế.

[Yếu tố loại trừ]
Phụ thuộc ảnh tham chiếu, phụ đề, khung giới hạn (bounding boxes), HUD, thiết kế rồng bay phương Tây, nhiều đầu, dị tật thừa chi, rung lắc dữ dội, dịch chuyển tức thời, chất liệu nhựa hoạt hình, watermark, Logo nền tảng, lời thoại, nhạc lấn át âm thanh môi trường.
```

## 4. Kiếm sĩ áo trắng đại chiến thủy long
**Model:** Seedance 2.0 · 30s · cảnh hành động, timecode từng giây · [link](https://youmind.com/vi-VN/video-prompts/white-robe-swordsman-dragon-battle-12487)

**Bản tiếng Việt:**

```text
Kiếm Sĩ Áo Trắng: Quyết Chiến Tại Vực Thẳm Rồng
[Act 1 | Triệu Hồi 0-10s]
0-3s: Một chiến binh áo trắng đứng trong vùng nước nông tại bờ sông hẻm núi tối tăm, bao quanh bởi các vách đá đen, với những doi cát nhạt màu ở phía xa. Ánh trăng lạnh lẽo chiếu xuyên qua các khe nứt trên đỉnh; mặt nước ẩm ướt, sương mù lan tỏa. Chiến binh cầm thanh trường kiếm đã rút khỏi vỏ trong tư thế sẵn sàng, tia chớp xanh-trắng chảy dọc theo lưỡi kiếm. Quay trung cảnh, dolly in chậm. Hạt sét, khí kiếm, sương từ thác nước.
3-5s: Chiến binh xoay người nhanh chóng và vung kiếm, các vòng cung sét kéo dài ra, sau đó lao về phía trước, bùng nổ sét. Quay tracking orbit chuyển thành góc nhìn rộng từ xa. Vệt sét, hình ảnh lưu (afterimage), nước bắn tung tóe, sóng xung kích sonic boom.
5-7.5s: Một con thủy long khổng lồ trỗi dậy từ mặt nước, khung xương hình thành, cơ thể nước định hình. Quay trung cảnh dolly. Hiệu ứng chất lỏng, râu rồng bay phất phơ, hồ quang điện quấn quanh.
7.5-10s: Con thủy long xoáy quanh chiến binh với tốc độ cao, lao thẳng vào camera, rồi quay lại sang bên cạnh. Mắt rồng phát sáng, nước bắn tung tóe. Quay tracking bên hông chuyển thành góc thấp ngước lên. Xoáy nước của rồng, sương mù, hạt dòng chảy nước.
[Act 2 | Trận Chiến Cuối Cùng 10-27s]
10-11.5s | Rồng Lao Xuống: Đầu rồng lao xuống, chiến binh bật dậy mạnh mẽ từ tư thế quỳ nửa, sóng xung kích làm bay áo trắng và mặt nước, trần kiếm bùng nổ sét, phản công đâm lên. Quay góc cực thấp ngước lên, bắt đầu từ chân gần mặt nước, camera xuyên qua ánh kiếm. Bùng nổ hướng tâm trên mặt nước, sét quá phơi sáng, vệt ánh sáng trắng.
11.5-13s | Va Chạm Chính Diện: Mũi kiếm va chạm với đầu rồng, sóng xung kích hình nhẫn nâng nước nông. Chiến binh lật mình giữa không trung, chém ba lần lướt qua đầu rồng, nén thành ba khoảnh khắc chớp nhoáng. Quay góc cực thấp ngước lên, xoay 180° cùng nhân vật, cắt sang góc nhìn trực diện 90° từ trên cao. Thân rồng và sét tạo thành một xoắn ốc khổng lồ trên mặt nước.
13-15s | Truy Đuổi Thân Rồng: Chiến binh chạy dọc thân rồng, dịch chuyển tức thời kiểu sét, mũi chân chạm nước gây bắn tung tóe. Đuôi rồng quét ngang, chiến binh cúi người né tránh, đảo ngược vòng xoay và chém ngang. Quay follow tốc độ cao góc siêu rộng 24mm, đuôi rồng che ống kính, camera roll. Hình ảnh lưu, nước bắn, vệt sét.
15-17s | Nhảy Cực Đoan: Chiến binh nhảy lên địa hình cao trong hẻm núi, co lại thành một chấm trắng trong bóng tối, sau đó rơi thẳng đứng đầu xuống như một tia sét trắng nhắm vào đầu rồng. Quay góc nhìn từ trên cao 90° trực diện, rơi với tốc độ tương tự, 0.2 giây cuối cùng cắt sang góc cực thấp gần mặt nước. Mặt đất, mặt nước, thân rồng phóng to nhanh chóng.
17-19s | Cận Chiến: Đầu rồng quay lại cắn, nanh vuốt lướt qua ống kính. Chiến binh dịch chuyển tức thời đi nơi khác, chém ngược ngang cổ rồng, thân rồng nổ tung thành dòng nước rồi tái hợp. Góc nhìn cực rộng 14-18mm, phối cảnh mạnh, camera xuyên qua miệng rồng, tốc độ thời gian thực suốt cả cảnh. Nổ dòng nước, giọt nước bắn tung tóe.
19-21.5s | Duel Bao Vây: Thủy long xoáy quanh chiến binh tạo thành xoáy nước rồng khổng lồ. Chiến binh ở tâm bão, chớp đến bốn vị trí mỗi lần chém một kiếm, bốn ánh kiếm trắng lạnh giao nhau thành mẫu hình sét. Orbit 360° tốc độ cao, độ cao thay đổi liên tục, đột ngột nâng lên từ mặt nước rồi lao thẳng xuống. Xoáy nước rồng, mẫu hình sét, hình ảnh lưu.
21.5-23.5s | Xung Phong Chính Diện: Rồng khổng lồ lao dọc toàn bộ chiều dài hẻm núi, kích thước lấp đầy màn hình. Chiến binh xung phong ngược lại một cách dữ dội, khoảng cách bằng không, biến mất ngay trước khi va chạm. Bố cục chính diện góc cực thấp, chiến binh là bóng trắng nhỏ xíu ở trung tâm. Đường tốc độ, sóng xung kích mặt nước.
23.5-25.5s | Chém Ngược Đầu Rồng: Chiến binh dịch chuyển tức thời lên trên đầu rồng, lật mình giữa không trung, cầm kiếm bằng hai tay và đánh xuống, sét xuyên qua toàn bộ cơ thể nước. Quay góc cực thấp ngước lên từ ngay dưới đầu rồng hướng tới bầu trời, camera đập thẳng xuống mặt nước theo mũi kiếm. Sét xuyên thấu, silhouette ngược sáng.
25.5-27s | Vụ Nổ Nước Tối Thượng: Ánh kiếm chạm nước, vùng nước nông nổ tung thành vụ nổ nước tròn khổng lồ. Thân rồng bị cắt thành vô số giọt nước lơ lửng, đóng băng ngắn ngủi rồi tất cả tán loạn. Cắt tức thì sang góc nhìn chim từ độ cao cực lớn 90°, tương phản quy mô. Giọt nước lơ lửng, vụ nổ nước hình nhẫn.
[Act 3 | Trở Về Im Lặng 27-30s]
Giọt nước rơi trở lại mặt sông, thần long tan biến thành sương mỏng. Chiến binh đứng giữa vùng nước nông, lưng quay về phía camera, tay phải hạ xuống tự nhiên, sét còn sót lại trên lưỡi kiếm dần tắt, áo trắng và tóc lay động nhẹ nhàng trong gió. Kéo lùi chậm từ phía sau nhân vật, hé lộ vùng nước hẻm núi tối tăm trống rỗng, chỉ còn lại gợn sóng cuối cùng. Sương mỏng, gợn sóng, sét mờ dần.
[Khóa Bắt Buộc] Trong suốt, chỉ giữ một thanh trường kiếm đã rút khỏi vỏ, không có vỏ kiếm, không có hành động rút/cất kiếm. Duy trì phong cách chiến binh áo trắng và môi trường hẻm núi tối tăm nhất quán. Không có đối thoại, không có thêm nhân vật, không có vũ khí mới.
```

## 5. Hài tiên hiệp: nghỉ 7 ngày, chân núi quá đông
**Model:** Seedance 2.0 · 30s · 16:9 · cần 2 ảnh nhân vật + ảnh bối cảnh · [link](https://youmind.com/vi-VN/video-prompts/seedance-xianxia-comedy-video-12488)

**Bản tiếng Việt:**

```text
Hài Kịch Tiên Hiệp Cổ Trang | Kỳ nghỉ bảy ngày, chân núi quá đông, trên núi phải trực đêm

I. Phong Cách Tổng Thể và Hướng Sáng Tạo

Kết cấu hình ảnh chân thực mang tính điện ảnh, thế giới tiên hiệp Trung Quốc cổ đại thuần túy, kịch bản hài hước đảo ngược lấy cảm hứng từ các mối quan hệ nhân vật đời thường.

Tiếp thu:

Sự lạnh lùng, hài hước kiểu Anh (deadpan humor)
Nhịp điệu hài kịch tình huống kiểu Hồng Kông
Khoảng lặng phản ứng và cấu trúc thiết lập - hồi phục của phim câm kinh điển

Diễn xuất nhân vật tự nhiên và tiết chế, không biểu cảm phóng đại, không chồng chất phép thuật cường điệu, không có những cảnh grandiose đột ngột.

Hình ảnh có:

Chất lượng quang học phim cổ điển chân thực
Chi tiết vi mô khuôn mặt rõ nét, tinh tế
Hạt phim tự nhiên
Vật lý vải vóc chân thực
Ánh sáng thể tích mềm mại
Các lớp không gian rõ ràng

II. Cốt Truyện Chính và Tuyến Hài Kịch

Toàn bộ phân đoạn chỉ có một tuyến hài kịch chính hoàn chỉnh kéo dài 30 giây:

Sư tỷ Kiếm Tiên và Sư muội cuối cùng cũng được nghỉ bảy ngày, hào hứng xuống núi tìm sự yên tĩnh, nhưng phát hiện mọi điểm tham quan đều xếp hàng dài, ngay cả kẻ thù đến khiêu chiến cũng phải xếp hàng.

Thất vọng, họ quay về môn phái sớm, chỉ để nhận ra rằng môn phái vắng vẻ mới là nơi yên tĩnh nhất; chưa kịp tận hưởng, Sư phụ đã giao nhiệm vụ trực đêm cho họ.

Họ lập tức quyết định quay lại xuống núi.

Câu chốt hạ (punchline) cuối cùng đến từ:

Sự tương phản thực tế: "Trước đây phàn nàn về đám đông, giờ lại thích đám đông hơn là trực đêm".

Nguyên tắc Hài Kịch

Tất cả các trò đùa đều được thúc đẩy bởi:

Lựa chọn của nhân vật

Chuyển động cơ thể
Thay đổi biểu cảm
Đối thoại tự nhiên
Không thể dựa vào tai nạn ma thuật ngẫu nhiên hoặc môi trường giải quyết vấn đề thay cho nhân vật.

III. Danh Tính Nhân Vật và Khóa Tài Sản

Sử dụng nghiêm ngặt @image1 và @image2 làm neo danh tính duy nhất cho hai nhân vật chính.

ID Nhân vật A | Sư Tỷ Kiếm Tiên

Luôn luôn là cùng một Sư Tỷ Kiếm Tiên trong @image1:

Nữ giới Đông Á, 25-30 tuổi

Cao gầy

Khuôn mặt trái xoan
Mắt hạnh nhân màu sẫm
Tóc đen dài buộc nửa, cố định bằng trâm ngọc bích trắng
Luôn mặc cùng một bộ Hán phục lụa thêu trắng
Tay áo rộng nhiều lớp
Đai lưng bạc
Mặt dây chuyền ngọc bích
Giày vải trắng
Cầm một thanh trường kiếm bạc độc đáo
ID Nhân vật B | Sư Muội
Luôn luôn là cùng một Sư Muội trong @image2:

Nữ giới Đông Á, 20-25 tuổi

Dáng người nhỏ nhắn

Khuôn mặt tròn sống động
Tóc đen tết bím
Luôn mặc cùng một bộ Hán phục vải lanh xanh lục
Đai tối màu
Trâm gỗ
Giày vải đen
Cầm một thanh kiếm thép tối màu độc đáo
Tính Liên Tục Của Nhân Vật
Các đặc điểm sau của hai nhân vật chính vẫn ổn định xuyên suốt:

Cấu trúc xương mặt

Các đường nét

Tỷ lệ cơ thể
Kiểu tóc
Quần áo
Vũ khí độc quyền
Nhân vật khác
Cùng một Sư phụ lớn tuổi

Một kiếm sĩ trẻ đối thủ

Cả hai đóng vai trò hỗ trợ chức năng.
IV. DNA Môi Trường và Quy Hoạch Không Gian

Tất cả hình ảnh nền và tham khảo địa điểm được tải lên trong vòng này cùng xác định một thế giới hình ảnh chân thực duy nhất.

Trước khi tạo, âm thầm tích hợp các yếu tố tương thích từ mỗi hình ảnh tham khảo:

Địa hình

Kiến trúc cổ

Vật liệu xây dựng

Quy mô không gian
Thực vật
Nguồn nước
Ánh sáng tự nhiên
Thời tiết
Mây/sương mù
Phản chiếu
Độ sâu không khí
Lối đi
Lập kế hoạch lại một bố cục không gian độc đáo và hợp lý về mặt vật lý cho vòng này.
Tính Liên Tục Địa Lý
Các địa điểm sau phải thuộc cùng một môi trường địa lý liên tục:

Cổng Núi

Đường Lên Núi

Đài Đá Ngắm Cảnh

Nguyên Nền Sống
Gió tự nhiên, dòng chảy nước, cành/lá, sương mỏng, phản chiếu và hoạt động của các đệ tử bình thường ở xa vẫn tồn tại nhưng giữ trung lập về mặt tường thuật, không chủ động tạo ra hoặc giải quyết các điểm nút cốt truyện.
Chỉ du khách được sắp xếp cụ thể và những người khiêu chiến đang chờ đợi mới có thể mang chức năng kịch tính cụ thể.

V. Cấu Trúc Storyboard Hoàn Chỉnh 30 Giây

00:00–00:06 | Phân đoạn 1: Cuối cùng cũng được nghỉ, nhưng Cổng Núi đông đúc

Cảnh quay: Góc máy toàn cảnh chậm tiến về phía Cổng Núi

Cảnh & Môi Trường

Ánh sáng buổi sáng tự nhiên, không khí núi trong lành.

Nước chảy xa, cành cây, tay áo và tóc dài đều chuyển động tự nhiên.

Hành Động & Đối Thoại Nhân Vật

Cùng một Sư phụ lớn tuổi bình tĩnh thông báo:

[Sư Phụ] "Hôm nay bắt đầu kỳ nghỉ bảy ngày."

Mắt của cùng một Sư Muội sáng rực lên ngay lập tức, lập tức quay sang Sư Tỷ:

[Sư Muội] "Sư tỷ, chúng ta đi tìm chỗ yên tĩnh nhé!"

Cùng một Sư Tỷ áo trắng trả lời dứt khoát:

[Sư Tỷ] "Đi thôi."

Hai người nhanh chóng đi về phía Cổng Núi với sự háo hức hiếm thấy.

Chuyển Động Camera

Camera theo dõi ổn định, dần dần tiết lộ các đệ tử cổ xưa khác và tu sĩ du hành rời khỏi cổng, tạo thành một đám đông tự nhiên, trật tự.

Nhịp Phản Ứng

Cả hai đồng thời chậm lại, chỉ trao đổi một cái nhìn nhẹ, không giải thích thêm.

00:06–00:12 | Phân đoạn 2: Biển mây đẹp, nhưng đầy người phía trước

Cảnh quay: Đài đá ngắm cảnh, góc máy trung và góc máy phản ứng

Thiết Kế Chuyển Cảnh

Sử dụng du khách đi ngang qua tiền cảnh để che chắn tự nhiên, chuyển cảnh sang đài đá ngắm cảnh trên núi trong cùng một thế giới tham khảo.

Cảnh & Không Gian

Biển mây và núi non xa xôi thực sự tráng lệ, nhưng có một hàng đợi hợp lý tại điểm ngắm cảnh tốt nhất.

Hành Động & Đối Thoại Nhân Vật

Cùng một Sư Muội kiễng chân nhẹ nhàng, cố gắng nhìn qua những người phía trước, hỏi với vẻ bối rối:

[Sư Muội] "Sư tỷ, biển mây đâu rồi?"

Cùng một Sư Tỷ cũng nghiêm túc nhìn vào đầu hàng đợi, dừng lại nửa nhịp, trả lời mà không thay đổi biểu cảm:

[Sư Tỷ] "Hàng người phía trước đó đang che mất nó."

Phản Ứng Hài Kịch

Sư Muội từ từ hạ gót chân xuống, kỳ vọng phai nhạt dần trên khuôn mặt.

Cho camera đủ khoảng lặng ngắn để khán giả tự cảm nhận sự chênh lệch.

00:12–00:18 | Phân đoạn 3: Ngay cả khiêu chiến Kiếm Tiên cũng phải xếp hàng

Cảnh quay: Góc máy trung hai người, lia ngang và tiết lộ không gian

Chuyển Động Camera

Camera lia tự nhiên dọc theo hai bên của họ.

Sự Xuất Hiện Của Kẻ Thù

Kiếm sĩ trẻ đối thủ duy nhất bước tới, trang trọng rút kiếm, hét lên với tinh thần khiêu chiến võ thuật chân thực:

[Kiếm Sĩ Đối Thủ] "Kiếm Tiên! Hôm nay chúng ta quyết chiến!"

Cùng một Sư Tỷ áo trắng cuối cùng cũng phấn chấn, đối mặt trực tiếp với đối thủ, trả lời:

[Sư Tỷ] "Được."

Đảo Ngược Hài Kịch Lần Đầu Tiên

Ngay khi họ chuẩn bị ra tay, cùng một Sư Muội tự nhiên giơ một tay giữa họ:

[Sư Muội] "Xếp hàng trước đã."

Kẻ thù đứng chết trân tại chỗ:

[Kiếm Sĩ Đối Thủ] "Đánh nhau cũng cần xếp hàng sao?"

Tiết Lộ Không Gian

Camera đơn giản lia theo ánh mắt của Sư Muội:

Hai người khiêu chiến cầm kiếm khác đang lặng lẽ chờ đợi bên cạnh bậc thang đá.

Một người ngồi

Một người đứng

Tất cả đều ở trạng thái cổ xưa chân thực, không tạo dáng phóng đại, không lặp lại ngoại hình của nhân vật chính.

Phản Ứng Hài Kịch

Kẻ thù từ từ hạ trường kiếm xuống, lần đầu tiên nói không nên lời.

Để lại đủ khoảng lặng phản ứng, không thêm lời thoại thừa.

00:18–00:24 | Phân đoạn 4: Về sớm, sự yên tĩnh trở thành hình phạt
Cảnh quay: Góc máy trung tại Cổng Núi, cảnh quay quan hệ ổn định

Thiết Kế Chuyển Cảnh

Sử dụng tay áo rộng trắng của Sư Tỷ đi ngang qua để che chắn tự nhiên.

Thời Gian & Cảnh

Buổi tối cùng ngày.

Họ quay lại chính xác kiến trúc và địa hình Cổng Núi giống như lúc đầu.

Hầu hết các đệ tử đã xuống núi, môn phái cực kỳ yên tĩnh, nhưng môi trường chắc chắn không phải là nền tĩnh:

Kênh nước tiếp tục chảy

Cành thông đung đưa trong gió

Bóng mây xa thay đổi chậm rãi

Hành Động & Đối Thoại Nhân Vật

Cùng một Sư Muội cuối cùng cũng ngồi thoải mái trên bậc thang đá quen thuộc, thư giãn và thở dài:

[Sư Muội] "Vẫn là yên tĩnh hơn ở Cổng Núi."

Cùng một Sư Tỷ đứng gần đó, thực sự thư giãn lần đầu tiên.

Bảo toàn hoàn toàn khoảng nửa giây của sự im lặng, tạm dừng cảm xúc thoải mái.
Đảo Ngược Hài Kịch Lần Thứ Hai
Cùng một Sư phụ lớn tuổi tiếp cận tự nhiên từ phía sau, cầm một cuộn văn bản khó đọc (danh sách trực ban bình thường), nói một cách thờ ơ:

[Sư Phụ] "Vậy thì hai con trực đêm nay."

Trọng Tâm Diễn Xuất

Sư Tỷ và Sư Muội gần như đồng thời từ từ quay đầu nhìn Sư phụ, sau đó trao đổi ánh mắt.

Không mở to mắt phóng đại.

Không hiệu ứng âm thanh bùng nổ.

Chỉ sử dụng biểu cảm phức tạp của hai người trưởng thành đột nhiên nhận ra mình đã nói sai để tạo ra sự đảo ngược.

00:24–00:30 | Phân đoạn 5: Thích xếp hàng tiếp hơn là trực đêm

Cảnh quay: Theo dõi trung ổn định, cuối cùng tập trung vào phản ứng của Sư phụ

Hành Động Nhân Vật

Sư Tỷ và Sư Muội không cần thảo luận, gần như đồng thời:

Đứng dậy

Điều chỉnh lại kiếm và quần áo ở eo

Đi nhanh về phía ngoài Cổng Núi

Đối Thoại

Cùng một Sư phụ nhìn họ với vẻ bối rối:

[Sư Phụ] "Chẳng phải các con phàn nàn về đám đông dưới kia sao?"

Cùng một Sư Muội không quay đầu lại, trả lời khi đang đi:

[Sư Muội] "Đông thì đông, nhưng không cần trực đêm."

Cùng một Sư Tỷ áo trắng bổ sung rất nghiêm túc:
[Sư Tỷ] "Đi nhanh lên, phòng trọ sẽ hết chỗ."
Chuyển Động Camera Cuối Cùng

Camera không đuổi theo hai người, mà ở lại tự nhiên trên Sư phụ.

Hãy để những bóng lưng ngày càng nhanh rời đi của hai người ở xa tạo thành tương phản thị giác với phản ứng im lặng của Sư phụ ở tiền cảnh.

Kết Thúc Hài Kịch Cuối Cùng

Sư phụ cuối cùng cũng nhận ra rằng ông đã đích thân gửi hai đệ tử phàn nàn về đám đông quay trở lại đám đông.

Ông chỉ thở dài nhẹ nhàng.

Kết thúc chính xác ở phản ứng bất lực hoàn toàn nhưng rất chân thực này.

VI. Yêu Cầu Tường Thuật Và Biên Tập Tổng Thể

Tổng thời lượng nghiêm ngặt 30 giây

Khung hình 16:9 ngang

Năm phân đoạn điện ảnh rõ ràng về nhịp độ và liên tục về nhân quả

Tất cả các địa điểm thuộc về một không gian địa lý hoàn chỉnh

Hoàn thành trực tiếp một tường thuật âm thanh-hình ảnh đầy đủ

Nguyên Tắc Cắt Cảnh

Việc chuyển đổi camera chỉ được hoàn thành tự nhiên thông qua:

Chuyển động của nhân vật

Che chắn tiền cảnh

Quan hệ ánh mắt
Tính liên tục của hướng
Không làm
```

## 6. Drone bay trong tranh sơn thủy xanh lục
**Model:** Grok Imagine · dọc · thiên về tranh, không phải người thật · [link](https://youmind.com/vi-VN/video-prompts/grok-imagine-chinese-landscape-drone-12805)

**Bản gốc (tiếng Trung):**

```text
Vertical cinematic drone shot flying forward inside a living Chinese blue-green landscape painting in the style of Wang Ximeng's Song-dynasty scroll "A Thousand Li of Rivers and Mountains": towering peaks painted in azurite blue and malachite green, misty clouds drifting between mountains, pine trees with twisted branches, tiny pavilions and boats on rivers, golden light filtering through haze, ink wash texture blending with realistic depth, slow majestic movement, ethereal atmosphere, high detail, 8k resolution
```

**Bản tiếng Việt:**

```text
Cảnh quay drone điện ảnh theo chiều dọc, bay tiến về phía trước bên trong một bức tranh sơn thủy Trung Hoa xanh biếc đang sống động, mang phong cách cuộn tranh "Thiên Lý Giang Sơn Đồ" thời Tống của Vương Hy Mạnh: những đỉnh núi cao vút được tô bằng màu xanh azurite và xanh lục malachite, mây mù lững lờ trôi giữa các rặng núi, cây tùng với cành lá uốn lượn, những đình đài nhỏ bé và thuyền bè trên sông, ánh sáng vàng xuyên qua lớp sương mờ, chất liệu thủy mặc hòa quyện cùng chiều sâu chân thực, chuyển động chậm rãi đầy uy nghi, bầu không khí phiêu diêu thoát tục, chi tiết sắc nét, độ phân giải 8k
```

## 7. Điện Mẫu, nữ thần sấm sét
**Model:** Grok Imagine · prompt ngắn, tạo ảnh rồi mở rộng thành video 20s · [link](https://youmind.com/vi-VN/video-prompts/dianmu-lightning-goddess-video-12520)

**Bản gốc:**

```text
Dianmu, the goddess said to wield lightning at will; she holds a flashing mirror in each hand. Please provide the image without any text. ⚡
```

**Bản tiếng Việt:**

```text
Dianmu, nữ thần được cho là có khả năng điều khiển sấm sét tùy ý; bà cầm một chiếc gương lóe sáng trong mỗi tay. Vui lòng cung cấp hình ảnh không kèm theo bất kỳ văn bản nào. ⚡
```
