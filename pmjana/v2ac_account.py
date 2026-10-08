# -*- coding: utf-8 -*-
"""v2ac(PMJ 自動分析エンジン)の勘定科目マスタ照合部分の写し

  元: pmj-aitext-1 / engine/v2ac.py (= test1 /var/www/html/pys/engine/v2ac.py)
    - class account_DB              (v2ac.py 112-761)
    - begin_symbol                  (v2ac.py 825)
    - add_candidate_syou / diff_syou* (v2ac.py 4279-4344。v2ac クラスのメソッドを関数にした)
  変更箇所には [pmj-ana] を付けている。照合のロジック自体は変えていない
  (add_candidate_syou の order だけ指定時に空になる不具合のみ修正)。
"""
import os
import re
import csv
import difflib
import traceback
from enum import IntEnum

begin_symbol = re.compile(r'^[0-9a-zA-Z]*')   # v2ac.py 825

remove_brackets = re.compile(r'[(（\[<【]?([^)）\]>】]+)[)）\]>】]*')
# 勘定科目DB
class account_DB(object):

    COMBINE_CODE         = '{:02}{:03}{:02}{:02}'      # ORDER:FAMILY:GENUS:SPECIES
    to_def_code_WO_order =       (999,  99,  99, 999)  #       FAMILY:GENUS:SPECIES:VARIETY
    blank_code_dict = {'order':-1, 'family':-1, 'genus':-1, 'species':-1, 'variety':-1, 'property':-1, 'variety_name':''}
    # 対象外のテーブルタイトル(仮) -> DBへ移行予定
    except_table_subject = [
            '棚卸資産の計算内訳',
            '製造原価報告書',
            'たな卸資産の計算内訳',
            '株主資本変動計算書'
    ]


    class Order(IntEnum):
        NON = 0
        PL  = 1
        BS  = 2
        MAX = 10

    class Code(IntEnum):
        ORDER   = 0
        FAMILY  = 1
        GENUS   = 2
        SPECIES = 3
        VARIETY = 4
        PROPERTY = 5
        MAX = 6
    class table_type(object):

        def __init__(self, type, end, keyword, score) -> None:
            self.order   = type
            self.end     = end
            self.keyword = keyword
            self.score   = score

    def __init__(self, master_json=None, revise_subject_path=None) -> None:
        # [pmj-ana] マスタはファイルからではなく、呼び出し側(GCS から取得)で読み込んだ JSON を受け取る

        self.order = ()
        self.family = ()
        self.genus = ()
        self.species = ()
        self.variety = ()
        self.variety_code = ()

        self.order_title = ()
        self.family_title = ()
        self.genus_title = ()
        self.species_title = ()
        self.variety_title = ()

        self.variety_title_alphabet = ()

        # キャッシュを保存 get_variety_title_list()
        self.cache_variety_title_list = ()
        self.cache_variety_title_list_code = (-999,)
        self.cache_variety_title_list_between = (-999,)

        self.table_keyword = (
            # PLの開始判定用	[損益計算書,売上高,売上高合計,売上原価]
            account_DB.table_type( account_DB.Order.PL, 0, '損益計算書',      1),
            account_DB.table_type( account_DB.Order.PL, 0, '売上高',          1),#
            account_DB.table_type( account_DB.Order.PL, 0, '売上高合計',      1),
            account_DB.table_type( account_DB.Order.PL, 0, '売上原価',        1),

            # PLの終了判定用	[当期利益,当期純利益,法人税,税引前当期純利益,税引前当期純損失]
            account_DB.table_type( account_DB.Order.PL, 1, '税引前当期純利益',1),
            account_DB.table_type( account_DB.Order.PL, 1, '税引前当期純損失',1),
            account_DB.table_type( account_DB.Order.PL, 1, '当期純利益',     1),
            account_DB.table_type( account_DB.Order.PL, 1, '当期利益',       1),
            account_DB.table_type( account_DB.Order.PL, 1, '法人税',         1),#

            # BSの開始判定用	[貸借対照表,流動資産,現金,預金,売掛金]
            account_DB.table_type( account_DB.Order.BS, 0, '貸借対照表',      1),
            account_DB.table_type( account_DB.Order.BS, 0, '流動資産',        1),
            account_DB.table_type( account_DB.Order.BS, 0, '売掛金',          1),
            account_DB.table_type( account_DB.Order.BS, 0, '現金',            1),#
            account_DB.table_type( account_DB.Order.BS, 0, '預金',            1),

            # BSの終了判定用	[負債純資産,負債・純資産,負債及び純資産,純資産,利益剰余金,利益準備金,資本剰余金,資本金]
            account_DB.table_type( account_DB.Order.BS, 1, '負債及び純資産', 1),
            account_DB.table_type( account_DB.Order.BS, 1, '負債・純資産',   1),
            account_DB.table_type( account_DB.Order.BS, 1, '負債純資産',     1),
            account_DB.table_type( account_DB.Order.BS, 1, '利益剰余金',     1),
            account_DB.table_type( account_DB.Order.BS, 1, '利益準備金',     1),
            account_DB.table_type( account_DB.Order.BS, 1, '資本剰余金',     1),
            account_DB.table_type( account_DB.Order.BS, 1, '純資産',         1),
            account_DB.table_type( account_DB.Order.BS, 1, '資本金',         1),
        )

        try:

            # [pmj-ana] マスターJSONは引数で受け取る(元: ../v2ac_kanjo_master.json を読む)
            if not master_json or 'kanjo_master' not in master_json:
                raise ValueError('勘定科目マスタ(kanjo_master)がありません')

            # タイトル(order=3)はCSVから読み込んで追加する
            # title_csv_path = os.path.join(os.path.dirname(__file__),'v2ac_title.csv')
            # with open(title_csv_path, 'r', encoding='utf_8', errors='', newline='') as f:
            #     data = csv.reader(f, delimiter=',', doublequote=True, lineterminator='\r\n', quotechar='"', skipinitialspace=True)
            #     for r in data:
            #         item = {"order":int(r[0]),"order_name":r[1],"family":int(r[2]),"family_name":r[3],"genus":int(r[4]),"genus_name":r[5],"variety":int(r[6]),"variety_name":r[7],"property":int(r[8]) }
            #         master_json['kanjo_master'].append(item)

            order   = []
            family  = []
            genus   = []
            species = []
            variety = []

            for item in master_json['kanjo_master']:
                order.append((int(item['order']),item['order_name']))
                # order.append((int(item['order']), item['order_name']))
                family.append((int(item['order']), int(item['family']), item['family_name']))
                genus.append((int(item['order']), int(item['family']), int(item['genus']), item['genus_name']))

                if 'species' not in item:
                    aaa = 0
                species.append((int(item['order']), int(item['family']), int(item['genus']), int(item['species']), item['species_name']))
                variety.append((int(item['order']), int(item['family']), int(item['genus']), int(item['species']), int(item['variety']), int(item['property']), item['variety_name']))
                if '＊' in item['variety_name']: # *(半角)も登録
                    variety_name = item['variety_name'].replace('＊','*')
                    variety.append((int(item['order']), int(item['family']), int(item['genus']), int(item['species']), int(item['variety']), int(item['property']), variety_name))

            # タイトルはCSVから読み込んで追加する
            # title_csv_path = os.path.join(os.path.dirname(__file__),'v2ac_title.csv')
            # with open(title_csv_path, 'r', encoding='utf_8', errors='', newline='') as f:
            #     data = csv.reader(f, delimiter=',', doublequote=True, lineterminator='\r\n', quotechar='"', skipinitialspace=True)
            #     for r in data:
            #         item = {"order":int(r[0]),"order_name":r[1],"family":int(r[2]),"family_name":r[3],"genus":int(r[4]),"genus_name":r[5],"variety":int(r[6]),"variety_name":r[7],"property":int(r[8]) }
            #         order.append((int(item['order']),item['order_name']))
            #         family.append((int(item['order']), int(item['family']), item['family_name']))
            #         genus.append((int(item['order']), int(item['family']), int(item['genus']), item['genus_name']))
            #         variety.append((int(item['order']), int(item['family']), int(item['genus']), int(item['variety']), int(item['property']), item['variety_name']))

            def unique_list(seq, unique=True):
                if unique:
                    # 重複を削除
                    uni = []
                    data1 = [x for x in seq if x not in uni and not uni.append(x)]
                else:
                    data1 = seq

                data2 = [x[-1] for x in data1]

                return tuple(data1), tuple(data2)

            self.order,   self.order_title   = unique_list(order)
            self.family,  self.family_title  = unique_list(family)
            self.genus,   self.genus_title   = unique_list(genus)
            self.species, self.species_title = unique_list(species)
            self.variety, self.variety_title = unique_list(variety,False)

            variety_code = []
            variety_title_alphabet = []
            alphabet_regex = re.compile(r'(^[A-Za-zＡ-Ｚａ-ｚ]+)(.*)')

            for t in self.variety:
                # FAMILY->3ケタ
                code_val   = int(account_DB.COMBINE_CODE.format(  t[account_DB.Code.ORDER],  t[account_DB.Code.FAMILY],  t[account_DB.Code.GENUS],  t[account_DB.Code.SPECIES]))
                # code_val   = int('{:02}{:02}{:02}{:03}'.format(  t[account_DB.Code.ORDER],  t[account_DB.Code.FAMILY],  t[account_DB.Code.GENUS],  t[account_DB.Code.SPECIES]))
                variety_code.append(code_val)

                ret = alphabet_regex.search(t[-1])
                if ret:
                    # variety_title_alphabet.append(t[-1])
                    # variety_title_alphabet.append({'text':t[-1], 'alphabet':ret.group(1)})
                    variety_title_alphabet.append((ret.group(0),ret.group(1),ret.group(2)))

            self.variety_code = tuple(variety_code)
            self.variety_title_alphabet = tuple(variety_title_alphabet)

            # [pmj-ana] 会社マスターは使わない(元のエンジンでも読み込むだけで未使用)

            # # マスターDBの読み込み
            # self.dbname = os.path.join(os.path.dirname(__file__),'v2ac.db')
            # conn = sqlite3.connect(self.dbname)
            # cur = conn.cursor()

            # def load(table_name):
            #     cur.execute(f'SELECT * FROM {table_name};')
            #     data1 = cur.fetchall()

            #     cur.execute(f'SELECT item FROM {table_name};')
            #     data2 = []
            #     for i in cur:
            #         data2.append(i[0])

            #     # data2.sort(key=lambda x: len(x), reverse=True) # ソートしても結果に変化なし

            #     return tuple(data1), tuple(data2)

            # self.order,    self.order_title =    load('type')
            # self.family,  self.family_title =  load('family')
            # self.genus,   self.genus_title =   load('genus')
            # # self.species, self.species_title = load('species')
            # self.variety, self.variety_title = load('variety')
            aaa = 0
        except:
            # [pmj-ana] 元は例外を握りつぶして空のマスタで動いていた。空のまま照合すると全行が未照合になるため止める
            traceback.print_exc()
            raise

        # "v2ac_subject"シートを ファイルの種類:CSV UTF-8(コンマ区切り)(*.csv) でエクスポート
        # 勘定科目置き換え(とりあえずCSVの読み込み)
        self.revise_subject_path = revise_subject_path or os.path.join(os.path.dirname(__file__),'v2ac_subject.csv')
        self.revise_subject_list = []
        try:
            # csv_file = open( self.revise_subject_path, 'r', encoding='cp932', errors='', newline='' )
            # csv_file = open( self.revise_subject_path, 'r', encoding='utf_8_sig', errors='', newline='' )
            csv_file = open( self.revise_subject_path, 'r', encoding='utf_8', errors='', newline='' )
            #リスト形式
            data = csv.reader(csv_file, delimiter=',', doublequote=True, lineterminator='\r\n', quotechar='"', skipinitialspace=True)

            for r in data:
                # r[0] = re.escape(r[0]) # 正規表現で指定するのでエスケープは不要
                self.revise_subject_list.append(r)
                aaa = 0

            self.revise_subject_list.sort(key=lambda x: len(x[0]), reverse=True)

            csv_file.close()
        except:
            pass

        aaa = 0

    # 勘定科目置き換え UNKNOWN
    def revise_subject(self, text):
        if not text:
            return None

        # 勘定科目置き換えリストと照合
        def search_revise_subject_list(t):
            for r in self.revise_subject_list:
                f = re.search(r[0], t)
                if f is not None:
                    return r

            return None

        ret = search_revise_subject_list(text)
        if ret:
            return ret

        # 全角以外を取り除く
        # not_zen = re.compile(f'[^{ZENKAKU}]')
        # f = not_zen.search(text)
        # if f:
        #     text2 = not_zen.sub('',text)
        #     if text == text2:
        #         return None

        #     ret = search_revise_subject_list(text2)
        #     if ret:
        #         # revise_subject_listにあれば返す
        #         return ret

        #     return [text, text2]

        return None

    def search_type(self, texts, ratio=0.7):

        for txt in texts:
            close = difflib.get_close_matches(txt, self.order_title,cutoff=ratio)
            if close:
                for r in self.order:
                    if r[1] == close[0]:
                        return r[0]

        return account_DB.Order.NON

    def search_type_title(self, title, ratio=0.7):

        close = difflib.get_close_matches( title, self.order_title,cutoff=ratio)

        return close
    def search_variety_title_syou(self, title, ratio=0.3, code=(), between=()):
        if title == '資産計io':
            aaa = 0
        # title = symbol.sub('',title) # 記号を削除 -> 削除すると精度が下がる可能性

        # title2 = begin_symbol.sub('',title) # 先頭のa-z0-9を削除
        # if title != title2:
        #     aaa = 0

        # 検索範囲を絞り込む
        title_list = self.get_variety_title_list(code, between)
        # title_list = self.get_variety_title_list(())

        title = begin_symbol.sub('',title) # 先頭のa-z0-9を削除

        # title1 = title
        # close1 = difflib.get_close_matches( title, title_list, cutoff=ratio)
        ## close1 = difflib.get_close_matches( title, self.variety_title,cutoff=ratio)

        l1 = len(title)

        title = remove_brackets.sub(r'\1', title) # カッコで囲まれている（精度下がる？？？）
        l2 = len(title)

        close = difflib.get_close_matches( title, title_list, n=40, cutoff=ratio)
        # close = difflib.get_close_matches( title, self.variety_title,cutoff=ratio)

        close=list(dict.fromkeys(close))
        
        for i, x in enumerate(close):
            if title==x :
                close[0], close[i] = close[i], close[0]

        if l1 != l2 and close:
            aaa = 0

        return close

    def search_variety_title(self, title, ratio=0.7, code=(), between=()):
        if title == '資産計io':
            aaa = 0
        # title = symbol.sub('',title) # 記号を削除 -> 削除すると精度が下がる可能性

        # title2 = begin_symbol.sub('',title) # 先頭のa-z0-9を削除
        # if title != title2:
        #     aaa = 0

        # 検索範囲を絞り込む
        title_list = self.get_variety_title_list(code, between)
        # title_list = self.get_variety_title_list(())

        title = begin_symbol.sub('',title) # 先頭のa-z0-9を削除

        # title1 = title
        # close1 = difflib.get_close_matches( title, title_list, cutoff=ratio)
        ## close1 = difflib.get_close_matches( title, self.variety_title,cutoff=ratio)

        l1 = len(title)

        title = remove_brackets.sub(r'\1', title) # カッコで囲まれている（精度下がる？？？）
        l2 = len(title)

        close = difflib.get_close_matches( title, title_list, cutoff=ratio)
        # close = difflib.get_close_matches( title, self.variety_title,cutoff=ratio)

        if l1 != l2 and close:
            aaa = 0

        return close

    # titleと勘定科目が"完全一致"
    def search_variety_code(self, title, code=(), between=()):
        code_len = len(code)
        between_len = len(between)

        if between_len == 0:
            for t in self.variety:
                if code == t[:code_len]:
                    if t[-1] == title:
                        return t[0:account_DB.Code.MAX]
        else:
            from_code  = code + ((0,) * (account_DB.Code.VARIETY+1-code_len))
            to_code = between + account_DB.to_def_code_WO_order[(between_len-1):]
            # to_code = between + ((0,) * (account_DB.Code.VARIETY+1-between_len))

            # FAMILY->3ケタ
            form_code_val = int(account_DB.COMBINE_CODE.format(from_code[account_DB.Code.ORDER],from_code[account_DB.Code.FAMILY],from_code[account_DB.Code.GENUS],from_code[account_DB.Code.SPECIES]))
            to_code_val   = int(account_DB.COMBINE_CODE.format(  to_code[account_DB.Code.ORDER],  to_code[account_DB.Code.FAMILY],  to_code[account_DB.Code.GENUS],  to_code[account_DB.Code.SPECIES]))
            # form_code_val = int('{:02}{:02}{:02}{:03}'.format(from_code[account_DB.Code.ORDER],from_code[account_DB.Code.FAMILY],from_code[account_DB.Code.GENUS],from_code[account_DB.Code.SPECIES]))
            # to_code_val   = int('{:02}{:02}{:02}{:03}'.format(  to_code[account_DB.Code.ORDER],  to_code[account_DB.Code.FAMILY],  to_code[account_DB.Code.GENUS],  to_code[account_DB.Code.SPECIES]))

            for i, c in enumerate(self.variety_code):
                if form_code_val <= c <= to_code_val:
                    if self.variety[i][-1] == title:
                        return self.variety[i][0:account_DB.Code.MAX]

        return () #[]
    def search_variety_codes(self, title, code=(), between=()):
        code_len = len(code)
        between_len = len(between)
        re=[]
        if between_len == 0:
            for t in self.variety:
                if code == t[:code_len]:
                    if t[-1] == title:
                        re.append(t[0:account_DB.Code.MAX])

        else:
            from_code  = code + ((0,) * (account_DB.Code.VARIETY+1-code_len))
            to_code = between + account_DB.to_def_code_WO_order[(between_len-1):]
            # to_code = between + ((0,) * (account_DB.Code.VARIETY+1-between_len))

            # FAMILY->3ケタ
            form_code_val = int(account_DB.COMBINE_CODE.format(from_code[account_DB.Code.ORDER],from_code[account_DB.Code.FAMILY],from_code[account_DB.Code.GENUS],from_code[account_DB.Code.SPECIES]))
            to_code_val   = int(account_DB.COMBINE_CODE.format(  to_code[account_DB.Code.ORDER],  to_code[account_DB.Code.FAMILY],  to_code[account_DB.Code.GENUS],  to_code[account_DB.Code.SPECIES]))
            # form_code_val = int('{:02}{:02}{:02}{:03}'.format(from_code[account_DB.Code.ORDER],from_code[account_DB.Code.FAMILY],from_code[account_DB.Code.GENUS],from_code[account_DB.Code.SPECIES]))
            # to_code_val   = int('{:02}{:02}{:02}{:03}'.format(  to_code[account_DB.Code.ORDER],  to_code[account_DB.Code.FAMILY],  to_code[account_DB.Code.GENUS],  to_code[account_DB.Code.SPECIES]))
            for i, c in enumerate(self.variety_code):
                if form_code_val <= c <= to_code_val:
                    if self.variety[i][-1] == title:
                        re.append(self.variety[i][0:account_DB.Code.MAX])

        return re

    # titleに勘定科目が"含まれる"
    def search_in_variety_code(self, title, ratio=0.7):

        for t in self.variety:
            if t[-1] in title:
                return t[0:account_DB.Code.MAX]

    # def search_variety_code(self, title, code=()):
    #     code_len = len(code)
    #     for t in self.variety:
    #         if code == t[:code_len]:
    #             if t[-1] == title:
    #                 return t[0:account_DB.Code.MAX]
    #                 # return t[0:4]
    #                 # return '{:01d}:{:02d}:{:02d}:{:03d}'.format(t[0],t[1],t[2],t[3])

    #     return () #[]

    # betweenを指定する場合はcodeは必須で長さも同じにする
    def get_variety_title_list(self, code=(), between=()):

        # 同じものが繰り返される可能性があるのでキャッシュしておく
        if code == self.cache_variety_title_list_code and between == self.cache_variety_title_list_between:
            data = self.cache_variety_title_list
        else:
            code_len = len(code)
            if code_len == 0:
                data = self.variety_title
            else:
                data = []
                between_len = len(between)
                if between_len == 0 or code_len != between_len:
                    # code だけで検索
                    for t in self.variety:
                        if code == t[:code_len]:
                            data.append(t[-1])
                else:
                    from_code  = code + ((0,) * (account_DB.Code.VARIETY+1-code_len))
                    # to_code = between + ((999,) * (account_DB.Code.VARIETY+1-between_len))
                    # FAMILY->3ケタ
                    # to_def_code = (999,99,99,999)
                    to_code = between + account_DB.to_def_code_WO_order[(between_len-1):]

                    # to_code = between + ((0,) * (account_DB.Code.VARIETY+1-between_len))

                    form_code_val = int(account_DB.COMBINE_CODE.format(from_code[account_DB.Code.ORDER],from_code[account_DB.Code.FAMILY],from_code[account_DB.Code.GENUS],from_code[account_DB.Code.SPECIES]))
                    to_code_val   = int(account_DB.COMBINE_CODE.format(  to_code[account_DB.Code.ORDER],  to_code[account_DB.Code.FAMILY],  to_code[account_DB.Code.GENUS],  to_code[account_DB.Code.SPECIES]))
                    # form_code_val = int('{:02}{:02}{:02}{:03}'.format(from_code[account_DB.Code.ORDER],from_code[account_DB.Code.FAMILY],from_code[account_DB.Code.GENUS],from_code[account_DB.Code.SPECIES]))
                    # to_code_val   = int('{:02}{:02}{:02}{:03}'.format(  to_code[account_DB.Code.ORDER],  to_code[account_DB.Code.FAMILY],  to_code[account_DB.Code.GENUS],  to_code[account_DB.Code.SPECIES]))

                    for i, c in enumerate(self.variety_code):
                        if form_code_val <= c <= to_code_val:
                            data.append(self.variety[i][-1])

                data = tuple(data)
            # キャッシュ
            self.cache_variety_title_list = data
            self.cache_variety_title_list_code = code
            self.cache_variety_title_list_between = between

        return data

    # def get_variety_title_list(self, code=()):

    #     # 同じものが繰り返される可能性があるのでキャッシュしておく
    #     if code == self.cache_variety_title_list_code:
    #         data = self.cache_variety_title_list
    #     else:
    #         code_len = len(code)
    #         if code_len == 0:
    #             data = self.variety_title
    #         else:
    #             data = []
    #             for t in self.variety:
    #                 if code == t[:code_len]:
    #                     data.append(t[-1])

    #             data = tuple(data)
    #         # キャッシュ
    #         self.cache_variety_title_list = data
    #         self.cache_variety_title_list_code = code

    #     return data

    # def search_variety_code(self, title):

    #     for t in self.variety:
    #         if t[-1] == title:
    #             return t[0:4]
    #             # return '{:01d}:{:02d}:{:02d}:{:03d}'.format(t[0],t[1],t[2],t[3])

    #     return []

    NO_REGISTRATION = '未登録'
    def code_to_title(self, code, data):

        code_len = len(code)
        for t in data:
            if t[:code_len] == code:
                return t[-1]

        return self.NO_REGISTRATION

    def code_to_variety_property(self, code):

        for t in self.variety:
            if t[:account_DB.Code.VARIETY+1] == code[:account_DB.Code.VARIETY+1]:
                return t[-2]

        return 0

    def code_to_title_list(self, code, data):

        title_list = []
        code_len = len(code)
        for t in data:
            if t[:code_len] == code:
                title_list.append(t[-1])

        return title_list

    def search_close_title(self, code, title, ratio=0.7):

        title_list = self.code_to_title_list(code, self.variety)

        close = difflib.get_close_matches( title, title_list,cutoff=ratio)

        return close

    # def type_to_title(self, code):

    #     for t in self.code:
    #         if t[0] == code[0]:
    #             return t[-1]

    #     return self.NO_REGISTRATION

    # def family_to_title(self, code):

    #     for t in self.family:
    #         if t[0] == code[0] and t[1] == code[2]:
    #             return t[-1]

    #     return self.NO_REGISTRATION

    # def family_to_title(self, code):

    #     for t in self.family:
    #         if t[0] == code[0] and t[1] == code[2]:
    #             return t[-1]

    #     return self.NO_REGISTRATION

    # def genus_to_title(self, code):

    #     for t in self.genus:
    #         if t[0] == code[0] and t[1] == code[2]:
    #             return t[-1]

    #     return self.NO_REGISTRATION

    def to_title_csv(self, code):

        title_csv = []

        code_len = len(code)

        if code_len >= account_DB.Code.ORDER+1:
            title = [code[account_DB.Code.ORDER], self.code_to_title(code[:account_DB.Code.ORDER+1], self.order)]
            title_csv += title
        if code_len >= account_DB.Code.FAMILY+1:
            title = [code[account_DB.Code.FAMILY], self.code_to_title(code[:account_DB.Code.FAMILY+1], self.family)]
            title_csv += title
        if code_len >= account_DB.Code.GENUS+1:
            title = [code[account_DB.Code.GENUS], self.code_to_title(code[:account_DB.Code.GENUS+1], self.genus)]
            title_csv += title
        if code_len >= account_DB.Code.SPECIES+1:
            title = [code[account_DB.Code.SPECIES], self.code_to_title(code[:account_DB.Code.SPECIES+1], self.species)]
            title_csv += title
        if code_len >= account_DB.Code.VARIETY+1:
            title = [code[account_DB.Code.VARIETY], self.code_to_title(code[:account_DB.Code.VARIETY+1], self.variety)]
            title_csv += title

        return title_csv

    def to_title_text(self, code):

        title_text = ''

        code_len = len(code)

        if code_len >= account_DB.Code.ORDER+1:
            title = '{}[{}]'.format(code[account_DB.Code.ORDER], self.code_to_title(code[:account_DB.Code.ORDER+1], self.order))
            title_text += title
        if code_len >= account_DB.Code.FAMILY+1:
            title = ':{}[{}]'.format(code[account_DB.Code.FAMILY], self.code_to_title(code[:account_DB.Code.FAMILY+1], self.family))
            title_text += title
        if code_len >= account_DB.Code.GENUS+1:
            title = ':{}[{}]'.format(code[account_DB.Code.GENUS], self.code_to_title(code[:account_DB.Code.GENUS+1], self.genus))
            title_text += title
        if code_len >= account_DB.Code.SPECIES+1:
            title = ':{}[{}]'.format(code[account_DB.Code.SPECIES], self.code_to_title(code[:account_DB.Code.SPECIES+1], self.species))
            title_text += title
        if code_len >= account_DB.Code.VARIETY+1:
            title = ':{}[{}]'.format(code[account_DB.Code.VARIETY], self.code_to_title(code[:account_DB.Code.VARIETY+1], self.variety))
            title_text += title

        return title_text

    # table_keywordの検索
    def search_table_keyword(self, row_txt):
        tks = []
        for tk in self.table_keyword:
            if tk.keyword in row_txt:
                tks.append(tk)
                # return tk

        return tks
        # return None

    # table_keywordの各要素のカウント
    def count_table_keyword(self):
        count = [0] * 4
        for tk in self.table_keyword:
            count[(tk.order-1)*2+tk.end] += 1
        return count



# ---- v2ac.py 4279-4344 (v2ac クラスのメソッド → 関数) ----
def add_candidate_syou(account_db, code_o=(), between_o=() ,val=""):
    title_list=account_db.get_variety_title_list(code=code_o, between=between_o)
    title_list_re=[]
    for t in title_list:
        if diff_syou_do(t,val) == True :
            title_list_re.append(t)
    if len(title_list_re) ==0 :
        for t in title_list:
            if diff_syou_soft_do(t,val) == True :
                title_list_re.append(t)
    candidate=[]
    for t in title_list_re:
        #code = account_db.search_variety_code(t,code=code_o, between=between_o)
        codes = account_db.search_variety_codes(t,code=code_o, between=between_o)
        if t == val :
            for code in codes :
                todoflag=False
                if len(code_o)>1 and len(between_o)>1 :
                    if code[1]>=code_o[1] and code[1]<=between_o[1] :
                        todoflag=True
                elif len(code_o)>1 :
                    if code[1]==code_o[1] :
                        todoflag=True
                elif len(code_o)==1 :   # [pmj-ana] order だけの指定(損益全体など)
                    todoflag=True
                if len(candidate)<600 and todoflag==True :
                    candidate.insert(0, {'order': code[0], 'family': code[1], 'genus': code[2], 'species': code[3], 'variety': code[4], 'property': code[5], 'variety_name': t})
        else :
            for code in codes :
                todoflag=False
                if len(code_o)>1 and len(between_o)>1 :
                    if code[1]>=code_o[1] and code[1]<=between_o[1] :
                        todoflag=True
                elif len(code_o)>1 :
                    if code[1]==code_o[1] :
                        todoflag=True
                elif len(code_o)==1 :   # [pmj-ana] order だけの指定(損益全体など)
                    todoflag=True
                if len(candidate)<60 and todoflag==True :
                    candidate.append({'order': code[0], 'family': code[1], 'genus': code[2], 'species': code[3], 'variety': code[4], 'property': code[5], 'variety_name': t})
    return candidate
def diff_syou_soft_do(str, target):
    total_l=len(target)
    if total_l < 3 :
        return diff_syou( str,target, 0.4)
    elif total_l == 3 :
        return diff_syou( str,target, 0.4)
    else :
        return diff_syou( str,target, 0.1)
def diff_syou(str, target,ratio):
    total_l=len(target)
    sum=0
    if total_l==0 :
        return False
    for t in target:
        if t in str :
            sum=sum+1
    if sum/total_l > ratio :
        return True
    else :
        return False
def diff_syou_do(str, target):
    total_l=len(target)
    if total_l < 3 :
        return diff_syou( str,target, 0.9)
    elif total_l == 3 :
        return diff_syou( str,target, 0.4)
    else :
        return diff_syou( str,target, 0.7)

