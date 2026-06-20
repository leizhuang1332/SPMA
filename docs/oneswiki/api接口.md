## 获取空间下的所有页面列表

```bash
curl --location --request GET 'https://ones.jtexpress.com.cn/wiki/api/wiki/team/5BXYuw3B/space/LdmzFdDE/pages' \
--header 'authorization: Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjNjOTM5NmZmLTczYjUtNDE5My00MDRlLTNiZGEzZDM2Njk4YSIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsib25lcy52MSJdLCJjbGllbnRfaW5mbyI6eyJjbGllbnRfaXAiOiIxMC4zMC44LjE3MCJ9LCJleHAiOjE3ODE5Mjc5MDAsImlhdCI6MTc4MTkyNDAwMCwiaXNzIjoiaHR0cDovL29uZXMuanRleHByZXNzLmNvbS5jbi8iLCJqdGkiOiI5MGViMGY4OS1hODExLTRiODYtNzhhYS1iYjNhNmVmMmE4ZTciLCJsb2dpbl90aW1lIjoxNzgxOTI0Mjk5NTQyLCJuYmYiOjE3ODE5MjQwMDAsIm9yZ191c2VyX3V1aWQiOiJHRVk5SEpLNSIsIm9yZ191dWlkIjoiRGFrNVJHV3oiLCJyZWdpb25fdXVpZCI6ImRlZmF1bHQiLCJzY29wZXMiOlsib3BlbmlkIiwib2ZmbGluZV9hY2Nlc3MiLCJvbmVzOm9yZzpkZWZhdWx0OkRhazVSR1d6OkdFWTlISks1Il0sInNpZCI6ImE4YzM5OWEyLTQyMmEtNDBjOC00NTY1LTY4YjRjZTQzMGVjMyIsInN1YiI6IkhlZ21tU2dVOmRlZmF1bHQ6RGFrNVJHV3o6R0VZOUhKSzUifQ.l9JL4Q2EUGJ0Or6m30BJn9vDQ-NgnpBZ1EkSh30kGqiUMWjTwQNhDOf-xBdmBrF9xllpigHotz0EmDbyn2CtCdF1oP36R_VDq3zW5BiH5O9TjuNw1gxKefTeHMVszIJN1ZfikOJndCiqSKoM7hiBGcjrZ7SC-hoDpzZ_qFnVcatj3vyfNEL2WqYsTIss5ri04_h9xCmsW-pFQcPZ4B-MJtsJnbd98lqX7k4unuEna4CCkq1dRMNipHBARWmAmSvjEGBBJEG5lQ52hpXtPWRXXz_b1l0qousjEVjF70pxL5PBojFFODof3l_hRw3yCvptdZmn2d7tNKQ4l9FdjJbEeQ' \
--header 'priority: u=1, i' \
--header 'traceparent: 00-405d36f46847229aea5ac57873916286-f4171e9ceca49d49-01' \
--header 'Cookie: language=zh; ones-lang=zh; ones-tz=Asia%2FShanghai; timezone=Asia/Shanghai; ones-ids-sid=179d0832-70a9-4dcf-4077-9c3af8c7b709; ones-region-uuid=default; ones-org-uuid=Dak5RGWz; ones-lt=eyJhbGciOiJSUzI1NiIsImtpZCI6IjNjOTM5NmZmLTczYjUtNDE5My00MDRlLTNiZGEzZDM2Njk4YSIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsib25lcy52MSJdLCJjbGllbnRfaW5mbyI6eyJjbGllbnRfaXAiOiIxMC4zMC44LjE3MCJ9LCJleHAiOjE3ODE5Mjc5MDAsImlhdCI6MTc4MTkyNDAwMCwiaXNzIjoiaHR0cDovL29uZXMuanRleHByZXNzLmNvbS5jbi8iLCJqdGkiOiI5MGViMGY4OS1hODExLTRiODYtNzhhYS1iYjNhNmVmMmE4ZTciLCJsb2dpbl90aW1lIjoxNzgxOTI0Mjk5NTQyLCJuYmYiOjE3ODE5MjQwMDAsIm9yZ191c2VyX3V1aWQiOiJHRVk5SEpLNSIsIm9yZ191dWlkIjoiRGFrNVJHV3oiLCJyZWdpb25fdXVpZCI6ImRlZmF1bHQiLCJzY29wZXMiOlsib3BlbmlkIiwib2ZmbGluZV9hY2Nlc3MiLCJvbmVzOm9yZzpkZWZhdWx0OkRhazVSR1d6OkdFWTlISks1Il0sInNpZCI6ImE4YzM5OWEyLTQyMmEtNDBjOC00NTY1LTY4YjRjZTQzMGVjMyIsInN1YiI6IkhlZ21tU2dVOmRlZmF1bHQ6RGFrNVJHV3o6R0VZOUhKSzUifQ.l9JL4Q2EUGJ0Or6m30BJn9vDQ-NgnpBZ1EkSh30kGqiUMWjTwQNhDOf-xBdmBrF9xllpigHotz0EmDbyn2CtCdF1oP36R_VDq3zW5BiH5O9TjuNw1gxKefTeHMVszIJN1ZfikOJndCiqSKoM7hiBGcjrZ7SC-hoDpzZ_qFnVcatj3vyfNEL2WqYsTIss5ri04_h9xCmsW-pFQcPZ4B-MJtsJnbd98lqX7k4unuEna4CCkq1dRMNipHBARWmAmSvjEGBBJEG5lQ52hpXtPWRXXz_b1l0qousjEVjF70pxL5PBojFFODof3l_hRw3yCvptdZmn2d7tNKQ4l9FdjJbEeQ; SERVERID=b6167a11de47194b26a6eee070b33bd7|1781926455|1781925636'

```
返回示例：

```json
{
    "pages": [
        {
            "uuid": "Wf7jz79A",
            "space_uuid": "LdmzFdDE",
            "owner_uuid": "",
            "title": "主页",
            "parent_uuid": "",
            "encrypt_status": 1,
            "is_can_edit": true,
            "ref_type": 6,
            "sub_ref_type": "",
            "ref_uuid": "K1GVmbxm",
            "updated_time": 1727402542,
            "creator": "Q3KH72iq",
            "archived": false,
            "locked": false,
            "tag_uuids": "",
            "CreatedTime": 1727402542
        },
        {
            "uuid": "MzeknQYK",
            "space_uuid": "LdmzFdDE",
            "owner_uuid": "",
            "title": "JMS（中国快递系统）",
            "parent_uuid": "Wf7jz79A",
            "encrypt_status": 1,
            "is_can_edit": true,
            "ref_type": 1,
            "sub_ref_type": "",
            "ref_uuid": "",
            "updated_time": 1625021512,
            "creator": "Vq9PoqcW",
            "archived": false,
            "locked": false,
            "tag_uuids": "",
            "CreatedTime": 1625018855
        },
        {
            "uuid": "QvjE921P",
            "space_uuid": "LdmzFdDE",
            "owner_uuid": "",
            "title": "JMS端",
            "parent_uuid": "MzeknQYK",
            "encrypt_status": 1,
            "is_can_edit": true,
            "ref_type": 1,
            "sub_ref_type": "",
            "ref_uuid": "",
            "updated_time": 1625019617,
            "creator": "Vq9PoqcW",
            "archived": false,
            "locked": false,
            "tag_uuids": "",
            "CreatedTime": 1625019617
        }
    ]
}
```

## 对结果进行过滤：递归获取所有子页面的 uuid 列表

1. 先找到 parent_uuid = "WtYqfpLF" 的记录，
2. 再从这些记录中提取出所有子页面的 uuid，
3. 最后返回这些子页面的 uuid 列表。

## 获取所有子页面的内容

```bash
curl --location --request GET 'https://ones.jtexpress.com.cn/wiki/api/wiki/team/5BXYuw3B/page/J7CuCDAs?action=view' \
--header 'authorization: Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjNjOTM5NmZmLTczYjUtNDE5My00MDRlLTNiZGEzZDM2Njk4YSIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsib25lcy52MSJdLCJjbGllbnRfaW5mbyI6eyJjbGllbnRfaXAiOiIxMC4zMC44LjE3MCJ9LCJleHAiOjE3ODE5MzE0MDUsImlhdCI6MTc4MTkyNzUwNSwiaXNzIjoiaHR0cDovL29uZXMuanRleHByZXNzLmNvbS5jbi8iLCJqdGkiOiI3OWVmZWE1MS03M2NlLTQyMjItNGVlMi02NzZhNGE0ZDgxMDgiLCJsb2dpbl90aW1lIjoxNzgxOTI0Mjk5NTQyLCJuYmYiOjE3ODE5Mjc1MDUsIm9yZ191c2VyX3V1aWQiOiJHRVk5SEpLNSIsIm9yZ191dWlkIjoiRGFrNVJHV3oiLCJyZWdpb25fdXVpZCI6ImRlZmF1bHQiLCJzY29wZXMiOlsib3BlbmlkIiwib2ZmbGluZV9hY2Nlc3MiLCJvbmVzOm9yZzpkZWZhdWx0OkRhazVSR1d6OkdFWTlISks1Il0sInNpZCI6ImE4YzM5OWEyLTQyMmEtNDBjOC00NTY1LTY4YjRjZTQzMGVjMyIsInN1YiI6IkhlZ21tU2dVOmRlZmF1bHQ6RGFrNVJHV3o6R0VZOUhKSzUifQ.OapvsiQyfG8Pb1q2Hb0IYvPytQuOBLsoAWxLMjWYzhSMQwW14TC3seTLva8ltM7ujtm6QyF8QPB0rR7IvjCJwTUP5-5ZnYonjK5Zs28opesUogbaMd22JsKWJsjZfK8jw9SLXOCUOFRgf8MIGp8IV0ZbXDbFciV73w2xAoQEx4J45GkvZn2sEujO_8vSm3ZCwDsPVHqec4Hy_eo2TKqDz094cSmvpHkgmwzHeeMTGoGDtyEk6ieITDfGzghxDTnU8DBdUNDoh1wzgS1iIm-9sBzsJOaUYGhcGoSHZ-redV7uYmTEJO4KkXqi7pMz3nIaPhGBmkHPQ7wr5RuQrxpUow' \
--header 'priority: u=1, i' \
--header 'traceparent: 00-d5b694772a6693a8be31db9a3891b443-04c9c3c2ebc066d4-01' \
--header 'Cookie: language=zh; ones-lang=zh; ones-tz=Asia%2FShanghai; timezone=Asia/Shanghai; ones-ids-sid=179d0832-70a9-4dcf-4077-9c3af8c7b709; ones-region-uuid=default; ones-org-uuid=Dak5RGWz; ones-lt=eyJhbGciOiJSUzI1NiIsImtpZCI6IjNjOTM5NmZmLTczYjUtNDE5My00MDRlLTNiZGEzZDM2Njk4YSIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsib25lcy52MSJdLCJjbGllbnRfaW5mbyI6eyJjbGllbnRfaXAiOiIxMC4zMC44LjE3MCJ9LCJleHAiOjE3ODE5MzE0MDUsImlhdCI6MTc4MTkyNzUwNSwiaXNzIjoiaHR0cDovL29uZXMuanRleHByZXNzLmNvbS5jbi8iLCJqdGkiOiI3OWVmZWE1MS03M2NlLTQyMjItNGVlMi02NzZhNGE0ZDgxMDgiLCJsb2dpbl90aW1lIjoxNzgxOTI0Mjk5NTQyLCJuYmYiOjE3ODE5Mjc1MDUsIm9yZ191c2VyX3V1aWQiOiJHRVk5SEpLNSIsIm9yZ191dWlkIjoiRGFrNVJHV3oiLCJyZWdpb25fdXVpZCI6ImRlZmF1bHQiLCJzY29wZXMiOlsib3BlbmlkIiwib2ZmbGluZV9hY2Nlc3MiLCJvbmVzOm9yZzpkZWZhdWx0OkRhazVSR1d6OkdFWTlISks1Il0sInNpZCI6ImE4YzM5OWEyLTQyMmEtNDBjOC00NTY1LTY4YjRjZTQzMGVjMyIsInN1YiI6IkhlZ21tU2dVOmRlZmF1bHQ6RGFrNVJHV3o6R0VZOUhKSzUifQ.OapvsiQyfG8Pb1q2Hb0IYvPytQuOBLsoAWxLMjWYzhSMQwW14TC3seTLva8ltM7ujtm6QyF8QPB0rR7IvjCJwTUP5-5ZnYonjK5Zs28opesUogbaMd22JsKWJsjZfK8jw9SLXOCUOFRgf8MIGp8IV0ZbXDbFciV73w2xAoQEx4J45GkvZn2sEujO_8vSm3ZCwDsPVHqec4Hy_eo2TKqDz094cSmvpHkgmwzHeeMTGoGDtyEk6ieITDfGzghxDTnU8DBdUNDoh1wzgS1iIm-9sBzsJOaUYGhcGoSHZ-redV7uYmTEJO4KkXqi7pMz3nIaPhGBmkHPQ7wr5RuQrxpUow; SERVERID=b6167a11de47194b26a6eee070b33bd7|1781928885|1781927804'
```
### 接口说明

- https://ones.jtexpress.com.cn/wiki/api/wiki/team/5BXYuw3B/page/{page_uuid}?action=view

- page_uuid 是页面的 uuid，例如：J7CuCDAs，即是上一步递归获取的所有子页面的 uuid。

### 返回示例

```json
{
    "uuid": "J7CuCDAs",
    "space_uuid": "LdmzFdDE",
    "owner_uuid": "KCBt5RPP",
    "title": "中转费账单优化20221206",
    "content": "<h2 data-oid=\"lhma0pyo\">1、中转费账单重量取值逻辑优化；</h2>\n\n<p>原逻辑：&nbsp; &nbsp; 同转运中心多次扫描，后上传较早扫描时间的，不做账单逻辑更新</p>\n\n<p><span style=\"color:#f76603\"><strong>优化逻辑：</strong></span>同转运中心多次扫描，后上传较早扫描时间的，到件扫描需要重新判断重量，<span style=\"color:#f76603\"><strong>发件不需要进行判断（发件不承重）</strong></span></p>\n\n<p>背景原因：计费规则为取最新扫描重量，为了较少系统压力，过滤不必要的判断，将后面上传扫描时间数据过滤不做处理；当前调整为取全程最大重量，需要将次部分重量一并进行判断</p>\n\n<p>&nbsp;</p>\n\n<p>考虑点：</p>\n\n<p>1、只更新重量</p>\n\n<p>2、扫描时间：第一次产生账单的扫描时间，记录第一条记录，不做覆盖；</p>\n\n<p>&nbsp;</p>\n\n<p>导出后台处理方案：（查询不变）</p>\n\n<p>A\\业务发生时间9月2号及之后，查询新分库分表</p>\n\n<p>B\\业务发生时间9月2号之前及跨时间段啊，查询历史库表+新分库分表；速度相对A较慢一点</p>\n\n<h2 data-oid=\"tijghi9f\">2、中转费账单增加业务发生时间导出</h2>\n\n<p><span style=\"font-size:14px\"><span style=\"color:black\"><span><span><span><span>中转费账单管理&mdash;&mdash;详情页：应收对账编辑、应付对账明细查询；日汇总、月汇总&mdash;&mdash;增加按业务发生时间导出功能</span></span></span></span></span></span></p>\n\n<p><span style=\"color:#f60002\"><strong><span style=\"font-size:14px\"><span style=\"background-color:#ffffff\">【共计6处】</span></span></strong></span></p>\n\n<p>&nbsp;</p>\n\n<figure class=\"ones-image-figure\" data-size=\"large\">\n<div class=\"image-wrapper\"><img data-mime=\"image/png\" data-or=\"1\" data-ref-id=\"HYecST5z\" data-ref-type=\"space\" data-size=\"large\" data-uuid=\"UBW7o6G8\" src=\"https://ones.jtexpress.com.cn/api/project/file/attachment/Fn7S5d3sEduEsJk8_0-md05mxNkw?imageMogr2/auto-orient&amp;e=1671780024&amp;token=VXu2kld82Q4CEhnpUzweXRgby4RUyIfxr11qICVo:vEe8zSFKQdr_tyx1Qq8yASS-k_k\" /></div>\n\n<figcaption></figcaption>\n</figure>\n\n<figure class=\"ones-image-figure\" data-size=\"medium\">\n<div class=\"image-wrapper\"><img data-mime=\"image/png\" data-or=\"1\" data-ref-id=\"HYecST5z\" data-ref-type=\"space\" data-size=\"medium\" data-uuid=\"8b3WAXft\" src=\"https://ones.jtexpress.com.cn/api/project/file/attachment/FsP9_VZJc2VRlO2i4aGTt426tWhS?imageMogr2/auto-orient&amp;e=1671780024&amp;token=VXu2kld82Q4CEhnpUzweXRgby4RUyIfxr11qICVo:SSiWtCNgys4xvUjG3KnHlGYYO8M\" /></div>\n\n<figcaption></figcaption>\n</figure>\n\n<figure class=\"ones-image-figure\" data-size=\"medium\">\n<div class=\"image-wrapper\"><img data-mime=\"image/png\" data-or=\"1\" data-ref-id=\"HYecST5z\" data-ref-type=\"space\" data-size=\"medium\" data-uuid=\"SAj44PCQ\" src=\"https://ones.jtexpress.com.cn/api/project/file/attachment/FqqgOieFtTO7Lgsy7MtGza9Ajbpn?imageMogr2/auto-orient&amp;e=1671780024&amp;token=VXu2kld82Q4CEhnpUzweXRgby4RUyIfxr11qICVo:Br4TrHYsy_7kw1NpMU_Zf-7xRIQ\" /></div>\n\n<figcaption></figcaption>\n</figure>\n\n<h2 data-oid=\"0k4xoq9g\">3、操作费账单增加业务发生时间导出</h2>\n\n<p><span style=\"color:#f60002\"><strong><span style=\"font-size:14px\"><span style=\"background-color:#ffffff\">【共计6处】</span></span></strong></span></p>\n\n<figure class=\"ones-image-figure\" data-size=\"medium\">\n<div class=\"image-wrapper\"><img data-mime=\"image/png\" data-or=\"1\" data-ref-id=\"HYecST5z\" data-ref-type=\"space\" data-size=\"medium\" data-uuid=\"4Ct4weXX\" src=\"https://ones.jtexpress.com.cn/api/project/file/attachment/FvgeZCPtAIJyVPOSHll4ol-BX36e?imageMogr2/auto-orient&amp;e=1671780024&amp;token=VXu2kld82Q4CEhnpUzweXRgby4RUyIfxr11qICVo:FIDk4spyjmdePGNV-4Y_J0Uf_Hw\" /></div>\n\n<figcaption></figcaption>\n</figure>\n\n<figure class=\"ones-image-figure\" data-size=\"medium\">\n<div class=\"image-wrapper\"><img data-mime=\"image/png\" data-or=\"1\" data-ref-id=\"HYecST5z\" data-ref-type=\"space\" data-size=\"medium\" data-uuid=\"UdLPLXcN\" src=\"https://ones.jtexpress.com.cn/api/project/file/attachment/FmlMTzfOAUNtvSwljx0iN7yjXtTO?imageMogr2/auto-orient&amp;e=1671780024&amp;token=VXu2kld82Q4CEhnpUzweXRgby4RUyIfxr11qICVo:0GULON1SMu4qvnwZylw3krck4HI\" /></div>\n\n<figcaption></figcaption>\n</figure>\n\n<figure class=\"ones-image-figure\" data-size=\"medium\">\n<div class=\"image-wrapper\"><img data-mime=\"image/png\" data-or=\"1\" data-ref-id=\"HYecST5z\" data-ref-type=\"space\" data-size=\"medium\" data-uuid=\"F5X4opqQ\" src=\"https://ones.jtexpress.com.cn/api/project/file/attachment/FkkWcqBlk0MaSx03vczQKuDjXQPo?imageMogr2/auto-orient&amp;e=1671780024&amp;token=VXu2kld82Q4CEhnpUzweXRgby4RUyIfxr11qICVo:0t2S-EzaQrUTogG4WrexEg_Z6mk\" /></div>\n\n<figcaption></figcaption>\n</figure>\n\n<p>&nbsp;</p>\n\n<p>&nbsp;</p>\n",
    "version": 4,
    "draft_uuid": "",
    "updated_time": 1671776477,
    "watch_users": [
        "KCBt5RPP"
    ],
    "encrypt_status": 1,
    "ref_type": 1,
    "sub_ref_type": "",
    "ref_uuid": "",
    "edit_users": null,
    "parent_uuid": "2TrihSVV",
    "archived": false,
    "is_fav": false,
    "public_info": {
        "public_type": 5,
        "public_type_source": "WtYqfpLF",
        "public_share_uuid": "",
        "public_user_uuid": "",
        "public_create_time": 0,
        "public_updated_time": 0
    },
    "CreatedTime": 1670293428,
    "Creator": "KCBt5RPP",
    "locked": false,
    "tag_uuids": "",
    "permissions": {
        "can_view": 1,
        "can_edit": 1,
        "can_view_history": 1,
        "can_manage_attachments": 1,
        "can_export": -1,
        "can_add_space_template": 1,
        "can_add_team_template": -1,
        "can_copy": 1,
        "can_move": 1,
        "can_delete": 1,
        "can_share": 1,
        "can_encrypt": -1,
        "can_archive": 1,
        "can_publish": 0,
        "can_download_attachment": 1,
        "can_insight": 0
    },
    "is_can_edit": true,
    "space_view_page_permission": true,
    "share_view_page_permission": false,
    "view_count": 19
}
```
## 要求说明
- content 是页面的内容，需要解析为干净的文档内容(去掉所有html标签，转换为markdown格式)。
- title 是页面的标题。保存到metadata中。
- updated_time、version 保存到metadata中。
- header authorization / Cookie: 可能会过期，需要做成参数，每次请求由用户传过来。