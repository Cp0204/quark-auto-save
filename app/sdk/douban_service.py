# -*- coding: utf-8 -*-
"""
豆瓣API服务模块
用于获取豆瓣影视榜单数据
"""

import requests
from typing import Dict, Optional, Any


class DoubanService:
    """豆瓣API服务类"""

    def __init__(self):
        self.base_url = "https://m.douban.com/rexxar/api/v2"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
            'Referer': 'https://m.douban.com/',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',  # 移除br，避免Brotli压缩
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin'
        }

        # 电影榜单配置 - 4个大类，每个大类下有5个小类
        self.movie_categories = {
            '热门电影': {
                '全部': {'category': '热门', 'type': '全部'},
                '华语': {'category': '热门', 'type': '华语'},
                '欧美': {'category': '热门', 'type': '欧美'},
                '韩国': {'category': '热门', 'type': '韩国'},
                '日本': {'category': '热门', 'type': '日本'}
            },
            '最新电影': {
                '全部': {'category': '最新', 'type': '全部'},
                '华语': {'category': '最新', 'type': '华语'},
                '欧美': {'category': '最新', 'type': '欧美'},
                '韩国': {'category': '最新', 'type': '韩国'},
                '日本': {'category': '最新', 'type': '日本'}
            },
            '豆瓣高分': {
                '全部': {'category': '豆瓣高分', 'type': '全部'},
                '华语': {'category': '豆瓣高分', 'type': '华语'},
                '欧美': {'category': '豆瓣高分', 'type': '欧美'},
                '韩国': {'category': '豆瓣高分', 'type': '韩国'},
                '日本': {'category': '豆瓣高分', 'type': '日本'}
            },
            '冷门佳片': {
                '全部': {'category': '冷门佳片', 'type': '全部'},
                '华语': {'category': '冷门佳片', 'type': '华语'},
                '欧美': {'category': '冷门佳片', 'type': '欧美'},
                '韩国': {'category': '冷门佳片', 'type': '韩国'},
                '日本': {'category': '冷门佳片', 'type': '日本'}
            }
        }

        # 剧集榜单配置 - 2个大类
        self.tv_categories = {
            '最近热门剧集': {
                '综合': {'category': 'tv', 'type': 'tv'},
                '国产剧': {'category': 'tv', 'type': 'tv_domestic'},
                '欧美剧': {'category': 'tv', 'type': 'tv_american'},
                '日剧': {'category': 'tv', 'type': 'tv_japanese'},
                '韩剧': {'category': 'tv', 'type': 'tv_korean'},
                '动画': {'category': 'tv', 'type': 'tv_animation'},
                '纪录片': {'category': 'tv', 'type': 'tv_documentary'}
            },
            '最近热门综艺': {
                '综合': {'category': 'show', 'type': 'show'},
                '国内': {'category': 'show', 'type': 'show_domestic'},
                '国外': {'category': 'show', 'type': 'show_foreign'}
            }
        }


    def get_list_data(self, main_category: str, sub_category: str, limit: int = 20, start: int = 0) -> Dict[str, Any]:
        """
        获取榜单数据

        Args:
            main_category: 主分类 (movie_hot, movie_latest, etc.)
            sub_category: 子分类 (全部, 华语, 欧美, etc.)
            limit: 返回数量限制
            start: 起始位置

        Returns:
            包含榜单数据的字典
        """
        try:
            # 判断是电影还是电视剧
            if main_category.startswith('movie_'):
                return self._get_movie_ranking(main_category, sub_category, start, limit)
            elif main_category.startswith('tv_'):
                return self._get_tv_ranking(main_category, sub_category, start, limit)
            else:
                return {
                    'success': False,
                    'message': f'不支持的主分类: {main_category}',
                    'data': {'items': []}
                }

        except Exception as e:
            return {
                'success': False,
                'message': f'获取榜单数据失败: {str(e)}',
                'data': {'items': []}
            }

    def _get_movie_ranking(self, main_category: str, sub_category: str, start: int = 0, limit: int = 20) -> Dict[str, Any]:
        """获取电影榜单数据"""
        try:

            # 映射主分类到豆瓣分类
            category_mapping = {
                'movie_hot': '热门电影',
                'movie_latest': '最新电影',
                'movie_top': '豆瓣高分',
                'movie_underrated': '冷门佳片'
            }

            douban_main_category = category_mapping.get(main_category, '热门电影')

            # 获取对应的category和type
            if douban_main_category not in self.movie_categories:
                # 使用模拟数据
                mock_data = self._get_mock_movie_data()
                return {
                    'success': True,
                    'message': '获取成功（模拟数据）',
                    'data': {
                        'items': mock_data['items'][:limit],
                        'total': len(mock_data['items']),
                        'is_mock_data': True,
                        'mock_reason': '不支持的分类，使用模拟数据'
                    }
                }

            category_config = self.movie_categories[douban_main_category].get(sub_category)
            if not category_config:
                # 使用模拟数据
                mock_data = self._get_mock_movie_data()
                return {
                    'success': True,
                    'message': '获取成功（模拟数据）',
                    'data': {
                        'items': mock_data['items'][:limit],
                        'total': len(mock_data['items']),
                        'is_mock_data': True,
                        'mock_reason': '不支持的子分类，使用模拟数据'
                    }
                }

            # 构建请求参数 - 按照参考项目的逻辑处理分类参数
            params = {'start': start, 'limit': limit}

            # 根据分类映射到豆瓣API的参数
            douban_category = category_config.get('category', '热门')
            douban_type = category_config.get('type', '全部')

            # 根据不同的category和type添加特定参数（完全按照参考项目的逻辑）
            if douban_category != '热门' or douban_type != '全部':
                if douban_type != '全部':
                    params['type'] = douban_type
                if douban_category != '热门':
                    params['category'] = douban_category

            # 尝试调用豆瓣API
            try:
                url = f"{self.base_url}/subject/recent_hot/movie"

                # 创建session来自动处理gzip解压
                session = requests.Session()
                session.headers.update(self.headers)

                response = session.get(url, params=params, timeout=30)
                response.raise_for_status()

                # 检查响应内容是否为空
                if not response.text.strip():
                    raise ValueError("API返回空响应")

                data = response.json()

                # 处理豆瓣移动端API的响应格式
                items = data.get('items', []) or data.get('subjects', [])

                if not items:
                    mock_data = self._get_mock_movie_data()
                    return {
                        'success': True,
                        'message': '获取成功（模拟数据）',
                        'data': {
                            'items': mock_data['items'][:limit],
                            'total': len(mock_data['items']),
                            'is_mock_data': True,
                            'mock_reason': 'API返回空数据'
                        }
                    }

                # 处理返回的数据
                processed_items = []
                for item in items[:limit]:
                    processed_item = self._process_item(item)
                    if processed_item:
                        processed_items.append(processed_item)

                return {
                    'success': True,
                    'message': '获取成功',
                    'data': {
                        'items': processed_items,
                        'total': data.get('total', len(processed_items))
                    }
                }

            except Exception:
                mock_data = self._get_mock_movie_data()
                return {
                    'success': True,
                    'message': '获取成功（模拟数据）',
                    'data': {
                        'items': mock_data['items'][:limit],
                        'total': len(mock_data['items']),
                        'is_mock_data': True,
                        'mock_reason': 'API调用失败'
                    }
                }

        except Exception as e:
            mock_data = self._get_mock_movie_data()
            return {
                'success': True,
                'message': '获取成功（模拟数据）',
                'data': {
                    'items': mock_data['items'][:limit],
                    'total': len(mock_data['items']),
                    'is_mock_data': True,
                    'mock_reason': f'获取失败: {str(e)}'
                }
            }

    def _get_tv_ranking(self, main_category: str, sub_category: str, start: int = 0, limit: int = 20) -> Dict[str, Any]:
        """获取电视剧榜单数据"""
        try:

            # 映射主分类到豆瓣分类
            category_mapping = {
                'tv_drama': '最近热门剧集',
                'tv_variety': '最近热门综艺'
            }

            douban_main_category = category_mapping.get(main_category, '最近热门剧集')

            # 获取对应的category和type
            if douban_main_category not in self.tv_categories:
                # 使用模拟数据
                mock_data = self._get_mock_tv_data()
                return {
                    'success': True,
                    'message': '获取成功（模拟数据）',
                    'data': {
                        'items': mock_data['items'][:limit],
                        'total': len(mock_data['items']),
                        'is_mock_data': True,
                        'mock_reason': '不支持的分类，使用模拟数据'
                    }
                }

            category_config = self.tv_categories[douban_main_category].get(sub_category)
            if not category_config:
                # 使用模拟数据
                mock_data = self._get_mock_tv_data()
                return {
                    'success': True,
                    'message': '获取成功（模拟数据）',
                    'data': {
                        'items': mock_data['items'][:limit],
                        'total': len(mock_data['items']),
                        'is_mock_data': True,
                        'mock_reason': '不支持的子分类，使用模拟数据'
                    }
                }

            # 构建请求参数 - 按照参考项目的逻辑处理分类参数
            params = {'start': start, 'limit': limit}

            # 根据分类映射到豆瓣API的参数
            douban_category = category_config.get('category', 'tv')
            douban_type = category_config.get('type', 'tv')

            # 根据不同的category和type添加特定参数（完全按照参考项目的逻辑）
            if douban_category != 'tv' or douban_type != 'tv':
                if douban_type != 'tv':
                    params['type'] = douban_type
                if douban_category != 'tv':
                    params['category'] = douban_category

            # 尝试调用豆瓣API
            try:
                url = f"{self.base_url}/subject/recent_hot/tv"

                # 创建session来自动处理gzip解压
                session = requests.Session()
                session.headers.update(self.headers)

                response = session.get(url, params=params, timeout=30)
                response.raise_for_status()

                # 检查响应内容是否为空
                if not response.text.strip():
                    raise ValueError("TV API返回空响应")

                data = response.json()

                # 处理豆瓣移动端API的响应格式
                items = data.get('items', []) or data.get('subjects', [])

                if not items:
                    mock_data = self._get_mock_tv_data()
                    return {
                        'success': True,
                        'message': '获取成功（模拟数据）',
                        'data': {
                            'items': mock_data['items'][:limit],
                            'total': len(mock_data['items']),
                            'is_mock_data': True,
                            'mock_reason': 'API返回空数据'
                        }
                    }

                # 处理返回的数据
                processed_items = []
                for item in items[:limit]:
                    processed_item = self._process_item(item)
                    if processed_item:
                        processed_items.append(processed_item)

                return {
                    'success': True,
                    'message': '获取成功',
                    'data': {
                        'items': processed_items,
                        'total': data.get('total', len(processed_items))
                    }
                }

            except Exception:
                mock_data = self._get_mock_tv_data()
                return {
                    'success': True,
                    'message': '获取成功（模拟数据）',
                    'data': {
                        'items': mock_data['items'][:limit],
                        'total': len(mock_data['items']),
                        'is_mock_data': True,
                        'mock_reason': 'API调用失败'
                    }
                }

        except Exception as e:
            mock_data = self._get_mock_tv_data()
            return {
                'success': True,
                'message': '获取成功（模拟数据）',
                'data': {
                    'items': mock_data['items'][:limit],
                    'total': len(mock_data['items']),
                    'is_mock_data': True,
                    'mock_reason': f'获取失败: {str(e)}'
                }
            }

    def _process_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        处理单个影片数据项

        Args:
            item: 原始数据项

        Returns:
            处理后的数据项
        """
        try:
            # 处理图片URL
            pic_data = item.get('pic', {})
            pic_url = ''
            if pic_data:
                # 优先使用normal尺寸的图片
                pic_url = pic_data.get('normal', '') or pic_data.get('large', '')

            # 处理评分数据
            rating_data = item.get('rating', {})
            rating = None
            if rating_data and rating_data.get('value'):
                rating = {'value': rating_data.get('value')}

            # 处理URL - 将douban://协议转换为标准HTTP链接
            original_url = item.get('url', '') or item.get('uri', '')
            processed_url = ''
            if original_url:
                if original_url.startswith('douban://douban.com/'):
                    # 将 douban://douban.com/movie/123 转换为 https://movie.douban.com/subject/123/
                    if '/movie/' in original_url:
                        # 提取ID部分
                        movie_id = original_url.split('/movie/')[-1]
                        processed_url = f'https://movie.douban.com/subject/{movie_id}/'
                    elif '/tv/' in original_url:
                        # 提取ID部分
                        tv_id = original_url.split('/tv/')[-1]
                        processed_url = f'https://movie.douban.com/subject/{tv_id}/'
                    else:
                        processed_url = original_url
                else:
                    processed_url = original_url

            processed = {
                'id': item.get('id', ''),
                'title': item.get('title', ''),
                'year': item.get('year', ''),
                'url': processed_url,
                'pic': {
                    'normal': pic_url
                },
                'rating': rating,
                'card_subtitle': item.get('card_subtitle', '')
            }

            # 确保必要字段存在
            if not processed['title']:
                return None

            return processed

        except Exception:
            return None

    def _get_mock_movie_data(self) -> Dict[str, Any]:
        """获取模拟电影数据"""
        return {
            'notice': "⚠️ 这是模拟数据，非豆瓣实时数据",
            'items': [
                {
                    'id': "1292052",
                    'title': "肖申克的救赎",
                    'rating': {'value': 9.7},
                    'year': "1994",
                    'url': "https://movie.douban.com/subject/1292052/",
                    'pic': {'normal': ""},
                    'card_subtitle': "1994 / 美国 / 剧情 犯罪 / 弗兰克·德拉邦特 / 蒂姆·罗宾斯 摩根·弗里曼"
                },
                {
                    'id': "1291546",
                    'title': "霸王别姬",
                    'rating': {'value': 9.6},
                    'year': "1993",
                    'url': "https://movie.douban.com/subject/1291546/",
                    'pic': {'normal': ""},
                    'card_subtitle': "1993 / 中国大陆 香港 / 剧情 爱情 同性 / 陈凯歌 / 张国荣 张丰毅"
                },
                {
                    'id': "1295644",
                    'title': "阿甘正传",
                    'rating': {'value': 9.5},
                    'year': "1994",
                    'url': "https://movie.douban.com/subject/1295644/",
                    'pic': {'normal': ""},
                    'card_subtitle': "1994 / 美国 / 剧情 爱情 / 罗伯特·泽米吉斯 / 汤姆·汉克斯 罗宾·怀特"
                }
            ],
            'total': 3
        }

    def _get_mock_tv_data(self) -> Dict[str, Any]:
        """获取模拟电视剧数据"""
        return {
            'notice': "⚠️ 这是模拟数据，非豆瓣实时数据",
            'items': [
                {
                    'id': "26794435",
                    'title': "请回答1988",
                    'rating': {'value': 9.7},
                    'year': "2015",
                    'url': "https://movie.douban.com/subject/26794435/",
                    'pic': {'normal': ""},
                    'card_subtitle': "2015 / 韩国 / 剧情 喜剧 家庭 / 申源浩 / 李惠利 朴宝剑"
                },
                {
                    'id': "1309163",
                    'title': "大明王朝1566",
                    'rating': {'value': 9.7},
                    'year': "2007",
                    'url': "https://movie.douban.com/subject/1309163/",
                    'pic': {'normal': ""},
                    'card_subtitle': "2007 / 中国大陆 / 剧情 历史 / 张黎 / 陈宝国 黄志忠"
                },
                {
                    'id': "1309169",
                    'title': "亮剑",
                    'rating': {'value': 9.3},
                    'year': "2005",
                    'url': "https://movie.douban.com/subject/1309169/",
                    'pic': {'normal': ""},
                    'card_subtitle': "2005 / 中国大陆 / 剧情 战争 / 陈健 张前 / 李幼斌 何政军"
                }
            ],
            'total': 3
        }


# 创建全局实例
douban_service = DoubanService()
