#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2015 Thomas Voegtlin
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import colorama
import time
import traceback
import webbrowser
import datetime
from datetime import date
from typing import TYPE_CHECKING
from collections import OrderedDict
from functools import partial

from electrum.address_synchronizer import TX_HEIGHT_LOCAL
from electrum.i18n import _
from electrum.util import block_explorer_URL, profiler, print_error, TxMinedStatus

from .util import *

if TYPE_CHECKING:
    from electrum.wallet import Abstract_Wallet

try:
    from electrum.plot import plot_history, NothingToPlotException
except:
    print_error("qt/history_list: could not import electrum.plot. This feature needs matplotlib to be installed.")
    plot_history = None

# note: this list needs to be kept in sync with another in kivy
TX_ICONS = [
    "unconfirmed.png",
    "warning.png",
    "unconfirmed.png",
    "offline_tx.png",
    "clock1.png",
    "clock2.png",
    "clock3.png",
    "clock4.png",
    "clock5.png",
    "confirmed.png",
]

class HistorySortModel(QSortFilterProxyModel):
    def lessThan(self, source_left: QModelIndex, source_right: QModelIndex):
        item1 = self.sourceModel().itemFromIndex(source_left).text()
        item2 = self.sourceModel().itemFromIndex(source_right).text()
        try:
            return float(item1) < float(item2)
        except ValueError:
            return item1 < item2

class HistoryList(QTreeView, AcceptFileDragDrop):
    filter_columns = [1, 2, 3]  # Date, Description, Amount

    def hide_row(self, proxy_row):
        for column in self.filter_columns:
            source_idx = self.proxy.mapToSource(self.proxy.index(proxy_row, column))
            txt = self.std_model.itemFromIndex(source_idx).text().lower()
            if self.current_filter in txt:
                self.setRowHidden(proxy_row, QModelIndex(), False)
                break
        else:
            self.setRowHidden(proxy_row, QModelIndex(), True)

    def filter(self, p):
        p = p.lower()
        self.current_filter = p
        for row in range(self.proxy.rowCount()):
            self.hide_row(row)

    def __init__(self, parent=None):
        QTreeView.__init__(self, parent)
        self.setUniformRowHeights(True)
        self.current_filter = ''
        self.blue_brush = QBrush(QColor("#1E1EFF"))
        self.red_brush = QBrush(QColor("#BC1E1E"))
        self.monospace_font = QFont(MONOSPACE_FONT)
        self.editable_columns = {2}
        self.parent = parent
        self.default_color = self.parent.app.palette().text().color()
        self.config = parent.config
        #MyTreeWidget.__init__(self, parent, self.create_menu, [], 3)
        AcceptFileDragDrop.__init__(self, ".txn")
        #self.setColumnHidden(1, True)
        self.setSortingEnabled(True)
        self.start_timestamp = None
        self.end_timestamp = None
        self.years = []
        self.create_toolbar_buttons()
        self.wallet = None
        self.stretch_column = 3

        self.proxy = HistorySortModel(self)
        self.std_model = QStandardItemModel(self)
        self.proxy.setSourceModel(self.std_model)
        self.setModel(self.proxy)
        root = self.std_model.invisibleRootItem()
        self.icon_cache = IconCache()

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.create_menu)

        self.setItemDelegate(ElectrumItemDelegate(self))

        self.initial()
        self.refresh_headers()
        self.sortByColumn(1, Qt.DescendingOrder)

    def keyPressEvent(self, event):
        if event.key() in [ Qt.Key_F2, Qt.Key_Return ]:
            self.edit(self.selectionModel().currentIndex().siblingAtColumn(2))
            return
        super().keyPressEvent(event)

    def createEditor(self, parent, option, index):
        editor = QStyledItemDelegate.createEditor(self.itemDelegate(),
                                                       parent, option, index)
        editor.editingFinished.connect(partial(self.on_edited, index))
        return editor

    def format_date(self, d):
        return str(datetime.date(d.year, d.month, d.day)) if d else _('None')

    def refresh_headers(self):
        headers = ['', _('Date'), _('Description'), _('Amount'), _('Balance')]
        fx = self.parent.fx
        if fx and fx.show_history():
            headers.extend(['%s '%fx.ccy + _('Value')])
            self.editable_columns |= {5}
            if fx.get_history_capital_gains_config():
                headers.extend(['%s '%fx.ccy + _('Acquisition price')])
                headers.extend(['%s '%fx.ccy + _('Capital Gains')])
        else:
            self.editable_columns -= {5}
        self.update_headers(headers)

    @profiler
    def update_headers(self, headers):
        print('update headers')
        col_count = self.std_model.columnCount()
        diff = col_count-len(headers)
        if col_count > len(headers):
            if diff == 2:
                self.std_model.removeColumns(6, diff)
            else:
                assert diff in [1, 3]
                self.std_model.removeColumns(5, diff)
            for items in self.txid_to_items.values():
                for _ in range(diff):
                    items.pop()
        elif col_count < len(headers):
            while self.std_model.rowCount():
                self.std_model.removeRow(0)
            self.initial()
        self.std_model.setHorizontalHeaderLabels(headers)
        self.header().setStretchLastSection(False)
        for col in range(len(headers)):
            sm = QHeaderView.Stretch if col == self.stretch_column else QHeaderView.ResizeToContents
            self.header().setSectionResizeMode(col, sm)

    def create_toolbar(self, config):
        pass

    def show_toolbar(self, show):
        pass

    def get_domain(self):
        '''Replaced in address_dialog.py'''
        return self.wallet.get_addresses()

    def on_combo(self, x):
        s = self.period_combo.itemText(x)
        x = s == _('Custom')
        self.start_button.setEnabled(x)
        self.end_button.setEnabled(x)
        if s == _('All'):
            self.start_timestamp = None
            self.end_timestamp = None
            self.start_button.setText("-")
            self.end_button.setText("-")
        else:
            try:
                year = int(s)
            except:
                return
            start_date = datetime.datetime(year, 1, 1)
            end_date = datetime.datetime(year+1, 1, 1)
            self.start_timestamp = time.mktime(start_date.timetuple())
            self.end_timestamp = time.mktime(end_date.timetuple())
            self.start_button.setText(_('From') + ' ' + self.format_date(start_date))
            self.end_button.setText(_('To') + ' ' + self.format_date(end_date))
        self.update()

    def create_toolbar_buttons(self):
        self.period_combo = QComboBox()
        self.start_button = QPushButton('-')
        self.start_button.pressed.connect(self.select_start_date)
        self.start_button.setEnabled(False)
        self.end_button = QPushButton('-')
        self.end_button.pressed.connect(self.select_end_date)
        self.end_button.setEnabled(False)
        self.period_combo.addItems([_('All'), _('Custom')])
        self.period_combo.activated.connect(self.on_combo)

    def get_toolbar_buttons(self):
        return self.period_combo, self.start_button, self.end_button

    def on_hide_toolbar(self):
        self.start_timestamp = None
        self.end_timestamp = None
        self.update()

    def save_toolbar_state(self, state, config):
        config.set_key('show_toolbar_history', state)

    def select_start_date(self):
        self.start_timestamp = self.select_date(self.start_button)
        self.update()

    def select_end_date(self):
        self.end_timestamp = self.select_date(self.end_button)
        self.update()

    def select_date(self, button):
        d = WindowModalDialog(self, _("Select date"))
        d.setMinimumSize(600, 150)
        d.date = None
        vbox = QVBoxLayout()
        def on_date(date):
            d.date = date
        cal = QCalendarWidget()
        cal.setGridVisible(True)
        cal.clicked[QDate].connect(on_date)
        vbox.addWidget(cal)
        vbox.addLayout(Buttons(OkButton(d), CancelButton(d)))
        d.setLayout(vbox)
        if d.exec_():
            if d.date is None:
                return None
            date = d.date.toPyDate()
            button.setText(self.format_date(date))
            return time.mktime(date.timetuple())

    def show_summary(self):
        h = self.summary
        if not h:
            self.parent.show_message(_("Nothing to summarize."))
            return
        start_date = h.get('start_date')
        end_date = h.get('end_date')
        format_amount = lambda x: self.parent.format_amount(x.value) + ' ' + self.parent.base_unit()
        d = WindowModalDialog(self, _("Summary"))
        d.setMinimumSize(600, 150)
        vbox = QVBoxLayout()
        grid = QGridLayout()
        grid.addWidget(QLabel(_("Start")), 0, 0)
        grid.addWidget(QLabel(self.format_date(start_date)), 0, 1)
        grid.addWidget(QLabel(str(h.get('start_fiat_value')) + '/BTC'), 0, 2)
        grid.addWidget(QLabel(_("Initial balance")), 1, 0)
        grid.addWidget(QLabel(format_amount(h['start_balance'])), 1, 1)
        grid.addWidget(QLabel(str(h.get('start_fiat_balance'))), 1, 2)
        grid.addWidget(QLabel(_("End")), 2, 0)
        grid.addWidget(QLabel(self.format_date(end_date)), 2, 1)
        grid.addWidget(QLabel(str(h.get('end_fiat_value')) + '/BTC'), 2, 2)
        grid.addWidget(QLabel(_("Final balance")), 4, 0)
        grid.addWidget(QLabel(format_amount(h['end_balance'])), 4, 1)
        grid.addWidget(QLabel(str(h.get('end_fiat_balance'))), 4, 2)
        grid.addWidget(QLabel(_("Income")), 5, 0)
        grid.addWidget(QLabel(format_amount(h.get('income'))), 5, 1)
        grid.addWidget(QLabel(str(h.get('fiat_income'))), 5, 2)
        grid.addWidget(QLabel(_("Expenditures")), 6, 0)
        grid.addWidget(QLabel(format_amount(h.get('expenditures'))), 6, 1)
        grid.addWidget(QLabel(str(h.get('fiat_expenditures'))), 6, 2)
        grid.addWidget(QLabel(_("Capital gains")), 7, 0)
        grid.addWidget(QLabel(str(h.get('capital_gains'))), 7, 2)
        grid.addWidget(QLabel(_("Unrealized gains")), 8, 0)
        grid.addWidget(QLabel(str(h.get('unrealized_gains', ''))), 8, 2)
        vbox.addLayout(grid)
        vbox.addLayout(Buttons(CloseButton(d)))
        d.setLayout(vbox)
        d.exec_()

    def plot_history_dialog(self):
        if plot_history is None:
            self.parent.show_message(
                _("Can't plot history.") + '\n' +
                _("Perhaps some dependencies are missing...") + " (matplotlib?)")
            return
        try:
            plt = plot_history(list(self.transactions.values()))
            plt.show()
        except NothingToPlotException as e:
            self.parent.show_message(str(e))

    @profiler
    def initial(self):
        self.wallet = self.parent.wallet  # type: Abstract_Wallet
        fx = self.parent.fx
        r = self.wallet.get_full_history(domain=self.get_domain(), from_timestamp=self.start_timestamp, to_timestamp=self.end_timestamp, fx=fx)
        self.transactions = OrderedDict([(x['txid'], x) for x in r['transactions']])
        self.summary = r['summary']
        if not self.years and self.transactions:
            start_date = next(iter(self.transactions.values())).get('date') or date.today()
            end_date = next(iter(reversed(self.transactions.values()))).get('date') or date.today()
            self.years = [str(i) for i in range(start_date.year, end_date.year + 1)]
            self.period_combo.insertItems(1, self.years)
        if fx: fx.history_used_spot = False
        self.txid_to_items = {}
        for tx_item in self.transactions.values():
            self.insert_tx(tx_item)

    #def assert_consistency(self):
    #    for idx, txid in list(enumerate(self.transactions.keys())):
    #        txid_in_row = self.std_model.item(idx).data(Qt.UserRole)
    #        assert txid_in_row == txid, (txid_in_row, txid, idx)

    def insert_tx(self, tx_item):
        fx = self.parent.fx
        tx_hash = tx_item['txid']
        height = tx_item['height']
        conf = tx_item['confirmations']
        timestamp = tx_item['timestamp']
        value = tx_item['value'].value
        balance = tx_item['balance'].value
        label = tx_item['label']
        tx_mined_status = TxMinedStatus(height, conf, timestamp, None)
        status, status_str = self.wallet.get_tx_status(tx_hash, tx_mined_status)
        has_invoice = self.wallet.invoices.paid.get(tx_hash)
        icon = self.icon_cache.get(":icons/" + TX_ICONS[status])
        v_str = self.parent.format_amount(value, is_diff=True, whitespaces=True)
        balance_str = self.parent.format_amount(balance, whitespaces=True)
        entry = ['', status_str, label, v_str, balance_str]
        cap_gains = self.parent.fx.get_history_capital_gains_config()
        history = self.parent.fx.show_history()
        if history:
            entry.append('')
        if cap_gains:
            entry.append('')
            entry.append('')
        fiat_value = None
        item = [QStandardItem(e) for e in entry]
        if has_invoice:
            item[2].setIcon(self.icon_cache.get(":icons/seal"))
        for i in range(len(entry)):
            self.set_item_properties(item[i], i, tx_hash)
        if value and value < 0:
            item[2].setForeground(self.red_brush)
            item[3].setForeground(self.red_brush)
        self.txid_to_items[tx_hash] = item
        self.update_item(tx_hash, self.parent.wallet.get_tx_height(tx_hash))
        if history:
            self.update_fiat(tx_hash, tx_item)
        source_row_idx = self.std_model.rowCount()
        self.std_model.insertRow(source_row_idx, item)
        self.hide_row(self.proxy.mapFromSource(self.std_model.index(source_row_idx, 0)).row())

    def set_item_properties(self, item, i, tx_hash):
        if i>2:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if i!=1:
            item.setFont(self.monospace_font)
        if i not in self.editable_columns:
            item.setEditable(False)
        item.setData(tx_hash, Qt.UserRole)

    #def ensure(self, items, idx, txid):
    #    while len(items) < idx + 1:
    #        qidx = self.std_model.index(idx, len(items))
    #        assert qidx.isValid()
    #        item = self.std_model.itemFromIndex(qidx)
    #        self.set_item_properties(item, len(items), txid)
    #        items.append(item)

    @profiler
    def update(self):
        print('update called', traceback.extract_stack()[-4])
        print('upper', traceback.extract_stack()[-5])
        print('upper', traceback.extract_stack()[-6])
        self.wallet = self.parent.wallet  # type: Abstract_Wallet
        fx = self.parent.fx
        r = self.wallet.get_full_history(domain=self.get_domain(), from_timestamp=self.start_timestamp, to_timestamp=self.end_timestamp, fx=fx)
        seen = set()
        history = fx.show_history()
        if r['transactions'] == list(self.transactions.values()):
            return
        #for i in range(self.std_model.columnCount()): self.hideColumn(i)
        for idx, row in enumerate(r['transactions']):
            txid = row['txid']
            seen.add(txid)
            if txid not in self.transactions:
                self.transactions[txid] = row
                self.transactions.move_to_end(txid, last=True)
                self.insert_tx(row)
                continue
            old = self.transactions[txid]
            self.update_item(txid, self.parent.wallet.get_tx_height(txid))
            if history:
                self.update_fiat(txid, row)
            balance_str = self.parent.format_amount(row['balance'].value, whitespaces=True)
            self.txid_to_items[txid][4].setText(balance_str)
            old.clear()
            old.update(**row)
        #for i in range(self.std_model.columnCount()): self.showColumn(i)
        removed = 0
        for idx, txid in list(enumerate(self.transactions.keys())):
            if txid not in seen:
                del self.transactions[txid]
                del self.txid_to_items[txid]
                items = self.std_model.takeRow(idx - removed)
                removed_txid = items[0].data(Qt.UserRole)
                assert removed_txid == txid, (idx, removed)
                removed += 1

    def update_fiat(self, txid, row):
        cap_gains = self.parent.fx.get_history_capital_gains_config()
        items = self.txid_to_items[txid]
        items[5].setForeground(self.blue_brush if not row['fiat_default'] and row['fiat_value'] else self.default_color)
        value_str = self.parent.fx.format_fiat(row['fiat_value'].value)
        items[5].setText(value_str)
        # fixme: should use is_mine
        if row['value'].value < 0 and cap_gains:
            items[6].setText(self.parent.fx.format_fiat(row['acquisition_price'].value))
            items[7].setText(self.parent.fx.format_fiat(row['capital_gain'].value))

    def update_on_new_fee_histogram(self):
        pass
        # TODO update unconfirmed tx'es

    def on_edited(self, index):
        column = index.column()
        index = self.proxy.mapToSource(index)
        item = self.std_model.itemFromIndex(index)
        key = item.data(Qt.UserRole)
        text = item.text()
        # fixme
        if column == 2:
            self.parent.wallet.set_label(key, text)
            self.update_labels()
            self.parent.update_completions()
        elif column == 5:
            self.parent.wallet.set_fiat_value(key, self.parent.fx.ccy, text)
            self.update_fiat(key, self.transactions[key])

    def on_doubleclick(self, item, column):
        if self.permit_edit(item, column):
            super(HistoryList, self).on_doubleclick(item, column)
        else:
            tx_hash = item.data(0, Qt.UserRole)
            self.show_transaction(tx_hash)

    def show_transaction(self, tx_hash):
        tx = self.wallet.transactions.get(tx_hash)
        if not tx:
            return
        label = self.wallet.get_label(tx_hash) or None # prefer 'None' if not defined (force tx dialog to hide Description field if missing)
        self.parent.show_transaction(tx, label)

    def update_labels(self):
        root = self.std_model.invisibleRootItem()
        child_count = root.rowCount()
        for i in range(child_count):
            item = root.child(i, 2)
            txid = item.data(Qt.UserRole)
            label = self.wallet.get_label(txid)
            item.setText(label)

    def update_item(self, tx_hash, tx_mined_status):
        if self.wallet is None:
            return
        conf = tx_mined_status.conf
        status, status_str = self.wallet.get_tx_status(tx_hash, tx_mined_status)
        icon = self.icon_cache.get(":icons/" +  TX_ICONS[status])
        if tx_hash not in self.txid_to_items:
            return
        items = self.txid_to_items[tx_hash]
        items[0].setIcon(icon)
        items[0].setToolTip(str(conf) + " confirmation" + ("s" if conf != 1 else ""))
        items[0].setData((status, conf), SortableTreeWidgetItem.DataRole)
        items[1].setText(status_str)

    def create_menu(self, position: QPoint):
        idx: QModelIndex = self.indexAt(position)
        idx = self.proxy.mapToSource(idx)
        item: QStandardItem = self.std_model.itemFromIndex(idx)
        assert item, 'create_menu: index not found in model'
        column_data = item.text()
        tx_hash = idx.data(Qt.UserRole)
        column = idx.column()
        assert tx_hash, "create_menu: no tx hash"
        tx = self.wallet.transactions.get(tx_hash)
        assert tx, "create_menu: no tx"
        column_title = self.std_model.horizontalHeaderItem(column).text()
        tx_URL = block_explorer_URL(self.config, 'tx', tx_hash)
        height = self.wallet.get_tx_height(tx_hash).height
        is_relevant, is_mine, v, fee = self.wallet.get_wallet_delta(tx)
        is_unconfirmed = height <= 0
        pr_key = self.wallet.invoices.paid.get(tx_hash)
        menu = QMenu()
        if height == TX_HEIGHT_LOCAL:
            menu.addAction(_("Remove"), lambda: self.remove_local_tx(tx_hash))
        menu.addAction(_("Copy {}").format(column_title), lambda: self.parent.app.clipboard().setText(column_data))
        for c in self.editable_columns:
            label = self.std_model.horizontalHeaderItem(c).text()
            editIdx = idx.siblingAtColumn(c)
            menu.addAction(_("Edit {}").format(label),
                           lambda bound_c=c: self.edit(editIdx))
        menu.addAction(_("Details"), lambda: self.show_transaction(tx_hash))
        if is_unconfirmed and tx:
            # note: the current implementation of RBF *needs* the old tx fee
            rbf = is_mine and not tx.is_final() and fee is not None
            if rbf:
                menu.addAction(_("Increase fee"), lambda: self.parent.bump_fee_dialog(tx))
            else:
                child_tx = self.wallet.cpfp(tx, 0)
                if child_tx:
                    menu.addAction(_("Child pays for parent"), lambda: self.parent.cpfp(tx, child_tx))
        if pr_key:
            menu.addAction(self.icon_cache.get(":icons/seal"), _("View invoice"), lambda: self.parent.show_invoice(pr_key))
        if tx_URL:
            menu.addAction(_("View on block explorer"), lambda: webbrowser.open(tx_URL))
        menu.exec_(self.viewport().mapToGlobal(position))

    def remove_local_tx(self, delete_tx):
        to_delete = {delete_tx}
        to_delete |= self.wallet.get_depending_transactions(delete_tx)
        question = _("Are you sure you want to remove this transaction?")
        if len(to_delete) > 1:
            question = _(
                "Are you sure you want to remove this transaction and {} child transactions?".format(len(to_delete) - 1)
            )
        answer = QMessageBox.question(self.parent, _("Please confirm"), question, QMessageBox.Yes, QMessageBox.No)
        if answer == QMessageBox.No:
            return
        for tx in to_delete:
            self.wallet.remove_transaction(tx)
        self.wallet.save_transactions(write=True)
        # need to update at least: history_list, utxo_list, address_list
        self.parent.need_update.set()

    def onFileAdded(self, fn):
        try:
            with open(fn) as f:
                tx = self.parent.tx_from_text(f.read())
                self.parent.save_transaction_into_wallet(tx)
        except IOError as e:
            self.parent.show_error(e)

    def export_history_dialog(self):
        d = WindowModalDialog(self, _('Export History'))
        d.setMinimumSize(400, 200)
        vbox = QVBoxLayout(d)
        defaultname = os.path.expanduser('~/electrum-history.csv')
        select_msg = _('Select file to export your wallet transactions to')
        hbox, filename_e, csv_button = filename_field(self, self.config, defaultname, select_msg)
        vbox.addLayout(hbox)
        vbox.addStretch(1)
        hbox = Buttons(CancelButton(d), OkButton(d, _('Export')))
        vbox.addLayout(hbox)
        #run_hook('export_history_dialog', self, hbox)
        self.update()
        if not d.exec_():
            return
        filename = filename_e.text()
        if not filename:
            return
        try:
            self.do_export_history(filename, csv_button.isChecked())
        except (IOError, os.error) as reason:
            export_error_label = _("Electrum was unable to produce a transaction export.")
            self.parent.show_critical(export_error_label + "\n" + str(reason), title=_("Unable to export history"))
            return
        self.parent.show_message(_("Your wallet history has been successfully exported."))

    def do_export_history(self, file_name, is_csv):
        history = self.transactions.values()
        lines = []
        if is_csv:
            for item in history:
                lines.append([item['txid'],
                              item.get('label', ''),
                              item['confirmations'],
                              item['value'],
                              item.get('fiat_value', ''),
                              item.get('fee', ''),
                              item.get('fiat_fee', ''),
                              item['date']])
        with open(file_name, "w+", encoding='utf-8') as f:
            if is_csv:
                import csv
                transaction = csv.writer(f, lineterminator='\n')
                transaction.writerow(["transaction_hash",
                                      "label",
                                      "confirmations",
                                      "value",
                                      "fiat_value",
                                      "fee",
                                      "fiat_fee",
                                      "timestamp"])
                for line in lines:
                    transaction.writerow(line)
            else:
                from electrum.util import json_encode
                f.write(json_encode(history))
